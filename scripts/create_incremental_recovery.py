"""Verify an incremental private recovery archive without staging large databases."""
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from dotenv import dotenv_values
from config.settings import ROOT
from scripts.create_recovery_backup import candidates,sha,check_live_credentials,safe_path

FORMAT='liquiditylab-incremental-recovery-v1'


def verify(bundle):
    with tarfile.open(bundle,'r:gz') as archive:
        first=archive.next()
        if first is None or first.name!='INCREMENTAL_MANIFEST.json' or not first.isfile() or first.size>16*1024*1024:
            raise ValueError('INCREMENTAL_MANIFEST_INVALID')
        manifest=json.load(archive.extractfile(first))
        if manifest.get('format')!=FORMAT:raise ValueError('INCREMENTAL_FORMAT_INVALID')
        expected={e['path']:e for e in manifest['changed_files']}
        if len(expected)!=len(manifest['changed_files']):raise ValueError('INCREMENTAL_DUPLICATE_PATH')
        seen=set()
        for member in archive:
            if member.name=='INCREMENTAL_MANIFEST.json':continue
            safe_path(Path(bundle).parent,member.name)
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError('INCREMENTAL_UNEXPECTED_MEMBER')
            entry=expected[member.name]
            if member.size!=entry['bytes']:raise ValueError('INCREMENTAL_SIZE_MISMATCH')
            h=hashlib.sha256()
            with archive.extractfile(member) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
            if h.hexdigest()!=entry['sha256']:raise ValueError('INCREMENTAL_HASH_MISMATCH')
            seen.add(member.name)
        if seen!=set(expected):raise ValueError('INCREMENTAL_MISSING_MEMBER')
    return dict(state='ALL_INCREMENTAL_MEMBERS_DECOMPRESSED_AND_HASH_VERIFIED',files=len(seen))


def create(base_folder,root=ROOT):
    root=Path(root).resolve();base_folder=Path(base_folder).resolve()
    base_folder.relative_to(root/'data/backups')
    base_result=json.loads((base_folder/'backup_result.json').read_bytes())
    base_bundle=safe_path(root,base_result['bundle'])
    if sha(base_bundle)!=base_result['sha256']:raise ValueError('INCREMENTAL_BASE_BUNDLE_CHANGED')
    with tarfile.open(base_bundle,'r:gz') as archive:
        member=archive.getmember('RECOVERY_MANIFEST.json')
        if not member.isfile() or member.size>16*1024*1024:raise ValueError('INCREMENTAL_BASE_MANIFEST_INVALID')
        raw=archive.extractfile(member).read();baseline=json.loads(raw)
    old={e['path']:e for e in baseline['files']}
    env=dotenv_values(root/'.env',interpolate=False)
    protected=[v for k,v in env.items() if re.search(r'password|secret|api_key|_token|vnstock_api|PENPOT_MCP_URL',k,re.I)]
    current=[];changed=[]
    for source,relative in candidates(root):
        name=relative.as_posix()
        if source.suffix=='.db':
            wal=Path(str(source)+'-wal')
            if wal.exists() and wal.stat().st_size:raise ValueError('INCREMENTAL_ACTIVE_SQLITE_WAL')
        entry=dict(path=name,bytes=source.stat().st_size,sha256=sha(source))
        current.append(entry)
        if name not in old or any(old[name][k]!=entry[k] for k in ('bytes','sha256')):
            check_live_credentials(source,protected);changed.append(entry)
    manifest=dict(format=FORMAT,created_at_utc=datetime.now(timezone.utc).isoformat(),
        base_bundle=base_result['bundle'],base_sha256=base_result['sha256'],
        base_manifest_sha256=hashlib.sha256(raw).hexdigest(),changed_files=changed,current_snapshot=current,
        removed_paths=sorted(set(old)-{e['path'] for e in current}),
        excludes=['credentials and state encryption key','active SSI recording journals','regenerable normalized.db'],
        restore_order='Restore the verified base into an empty directory, then overlay this delta; apply removed_paths only inside that new restore directory.',
        cloud_uploaded=False,originals_deleted=False,full_canonical_rebuild_verified=False)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    folder=root/'data/backups'/('incremental-'+stamp);folder.mkdir()
    bundle=folder/'liquiditylab-incremental.tar.gz'
    with tarfile.open(bundle,'w:gz',compresslevel=6) as archive:
        content=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode()
        info=tarfile.TarInfo('INCREMENTAL_MANIFEST.json');info.size=len(content)
        archive.addfile(info,io.BytesIO(content))
        for entry in changed:
            source=safe_path(root,entry['path'])
            if sha(source)!=entry['sha256']:raise ValueError('INCREMENTAL_SOURCE_CHANGED')
            archive.add(source,arcname=entry['path'],recursive=False)
            if sha(source)!=entry['sha256']:raise ValueError('INCREMENTAL_SOURCE_CHANGED')
    proof=verify(bundle)
    result=dict(state='INCREMENTAL_ARCHIVE_VERIFIED',bundle=bundle.relative_to(root).as_posix(),
        bytes=bundle.stat().st_size,sha256=sha(bundle),changed_files=len(changed),snapshot_files=len(current),
        base_bundle=base_result['bundle'],base_sha256=base_result['sha256'],verification=proof,
        originals_deleted=False,cloud_uploaded=False,full_chain_restore_verified=False)
    (folder/'backup_result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (root/'report/recovery_incremental_latest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    print(json.dumps(create(ROOT/'data/backups/recovery-20261112_161833_403806')))
