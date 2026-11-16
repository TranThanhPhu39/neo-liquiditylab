"""Combine a restored base with actually restored required incremental files."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile

from scripts.cleanup_dashboard_storage import backup_verified
from scripts.create_recovery_backup import safe_path,sha
from scripts.build_orderbook_monitor import atomic


def verified_recovery_files(root,required):
    root=Path(root).resolve();saved=backup_verified(root)
    needed={p:h for p,h in required.items() if saved.get(p)!=h}
    if not needed:return saved
    result_path=root/'report/recovery_incremental_latest.json'
    if not result_path.exists():return saved
    result=json.loads(result_path.read_bytes());bundle=safe_path(root,result['bundle'])
    if result.get('state')!='INCREMENTAL_ARCHIVE_VERIFIED' or sha(bundle)!=result['sha256']:
        raise ValueError('LEGACY_INCREMENTAL_BACKUP_NOT_VERIFIED')
    restored={};size=0
    with tempfile.TemporaryDirectory(prefix='legacy-recovery-',dir=root/'data/control') as temporary:
        target=Path(temporary).resolve()
        if target.parent!=(root/'data/control'):raise ValueError('LEGACY_RESTORE_UNSAFE_TARGET')
        with tarfile.open(bundle,'r|gz') as archive:
            first=archive.next()
            if first is None or first.name!='INCREMENTAL_MANIFEST.json':raise ValueError('LEGACY_INCREMENTAL_MANIFEST_MISSING')
            manifest=json.load(archive.extractfile(first));entries={e['path']:e for e in manifest['changed_files']}
            for member in archive:
                if member.name not in needed:continue
                entry=entries.get(member.name)
                if (not member.isfile() or not entry or member.size!=entry['bytes'] or
                    entry['sha256']!=needed[member.name] or member.name in restored):
                    raise ValueError('LEGACY_INCREMENTAL_REQUIRED_FILE_INVALID')
                path=safe_path(target,member.name);path.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as source,path.open('xb') as output:
                    for chunk in iter(lambda:source.read(1024*1024),b''):output.write(chunk)
                if sha(path)!=needed[member.name]:raise ValueError('LEGACY_INCREMENTAL_RESTORE_HASH_MISMATCH')
                restored[member.name]=needed[member.name];size+=member.size
    atomic(root/'report/legacy_delta_restore_check.json',dict(state='REQUIRED_INCREMENTAL_FILES_RESTORED_AND_HASH_VERIFIED',
        bundle=result['bundle'],bundle_sha256=result['sha256'],files=len(restored),bytes=size,
        required_files=len(needed),all_required_restored=len(restored)==len(needed),source_files_changed=False))
    saved.update(restored);return saved
