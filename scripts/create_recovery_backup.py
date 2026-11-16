"""Private, compact recovery bundle. Excludes credentials and regenerable large DBs."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import sqlite3
import tarfile

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.scheduled_collection import exclusive_job

FORMAT='liquiditylab-private-recovery-v1'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def check_live_credentials(path,values):
    secrets=[v.encode() for v in values if isinstance(v,str) and len(v)>=8]
    if not secrets:return
    overlap=max(map(len,secrets));tail=b''
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            combined=tail+chunk
            if any(value in combined for value in secrets):raise ValueError('BACKUP_LIVE_CREDENTIAL_FOUND')
            tail=combined[-overlap:]


def candidates(root):
    files=set()
    for folder in ('src','scripts','config','tests','docs','web/monitor'):
        for p in (root/folder).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.js','.cjs','.css','.html','.md','.json','.yaml','.yml','.txt') and '__pycache__' not in p.parts:
                files.add(p)
    for name in ('README.md','pyproject.toml','requirements.txt','.gitignore'):
        if (root/name).is_file():files.add(root/name)
    processed_manifest=root/'data/processed/dnse/manifest.json'
    if processed_manifest.exists():files.add(processed_manifest)
    ssi_contract=root/'data/control/ssi_live_contract.json'
    if ssi_contract.exists():files.add(ssi_contract)
    # An active SSI journal keeps changing. Back up closed sessions after Dừng & lưu.
    for manifest in (root/'data/realtime/ssi').glob('????-??-??/*/manifest.json'):
        value=json.loads(manifest.read_bytes())
        if value.get('state')=='RECORDING':continue
        files.add(manifest)
        for item in value.get('files',[]):
            if Path(item['path']).name!=item['path']:raise ValueError('BACKUP_SSI_PATH_INVALID')
            files.add(manifest.parent/item['path'])
        if (manifest.parent/'recovery-tail.bin').exists():files.add(manifest.parent/'recovery-tail.bin')
    for folder in ('data/history/dnse','data/history/dnse-trades','data/raw','data/realtime/dnse'):
        for p in (root/folder).rglob('*'):
            if p.is_file() and p.suffix in ('.db','.json','.gz','.jsonl','.csv') and not p.name.endswith('.tmp'):files.add(p)
    for pattern in ('dnse*rules*.json','dnse_manual_checks_*.json','dnse_ws_catalog.json','orderbook-monitor-reviews.db'):
        files.update((root/'data/control').glob(pattern))
    for pattern in ('dnse*unit*.json','dnse*image*.json','dnse_normalization_audit.json','dnse_normalization_finance_checks.json',
                    'dnse_normalization.json','dnse_trades_collection.json','trade_bubbles_build.json'):
        files.update((root/'report').glob(pattern))
    catalog=root/'data/compact/catalog.json'
    if catalog.exists():
        files.add(catalog);index=json.loads(catalog.read_text(encoding='utf-8'))
        folders=[]
        for e in index.get('quote_days',[])+index.get('trade_packages',[]):
            if not e['folder'].startswith('data/compact/'):raise ValueError('BACKUP_COMPACT_PATH_INVALID')
            folders.append(safe_path(root,e['folder']))
        manifests=[folder/'manifest.json' for folder in folders]
    else:manifests=(root/'data/compact/dnse').glob('*/manifest.json')
    manifests=list(manifests)
    monitor=root/'data/exports/orderbook-monitor/index.json'
    if monitor.exists():
        files.add(monitor)
        for entry in json.loads(monitor.read_text(encoding='utf-8')).get('datasets',[]):
            if entry.get('compact_folder'):
                manifests.append(safe_path(root,entry['compact_folder'])/'manifest.json')
            if entry.get('events_file'):files.add(safe_path(monitor.parent,entry['events_file']))
    for manifest in manifests:
        value=json.loads(manifest.read_text(encoding='utf-8'))
        if value.get('format') in ('liquiditylab-lossless-parquet-day-v1','liquiditylab-lossless-parquet-trades-v1','liquiditylab-parquet-replay-v1','liquiditylab-parquet-replay-v2'):
            files.add(manifest)
            if (manifest.parent/'verification.json').exists():files.add(manifest.parent/'verification.json')
            for e in value['files']:
                if Path(e['path']).name!=e['path']:raise ValueError('BACKUP_COMPACT_PATH_INVALID')
                files.add(manifest.parent/e['path'])
    for path in sorted(files):
        rel=path.relative_to(root)
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=root and root in p.parents):raise ValueError('BACKUP_SYMLINK_NOT_ALLOWED')
        if any(part.startswith('.env') or part in ('.git','.aws','.codex','.agents') for part in rel.parts):continue
        if path.suffix=='.dpapi' or any(word in path.name.lower() for word in ('credentials','private_key','access_token','api_secret')):continue
        yield path,rel


def safe_path(base,name):
    p=PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or any(x in ('..','.') or ':' in x for x in p.parts):raise ValueError('BACKUP_UNSAFE_PATH')
    target=base.joinpath(*p.parts).resolve()
    target.relative_to(base.resolve())
    return target


def create(root=ROOT):
    root=Path(root).resolve();stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    folder=root/'data/backups'/('recovery-'+stamp);stage=folder/'payload';stage.mkdir(parents=True,exist_ok=False)
    entries=[]
    from dotenv import dotenv_values
    env=dotenv_values(root/'.env') if (root/'.env').exists() else {}
    protected=[v for k,v in env.items() if re.search(r'password|secret|api_key|_token|vnstock_api|PENPOT_MCP_URL',k,re.I)]
    with exclusive_job(root/'data/control/recovery-backup.lock'):
        for source,rel in candidates(root):
            target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True)
            # Raw history has registered byte hashes: preserve bytes rather than repack SQLite.
            history=rel.as_posix().startswith('data/history/')
            wal=Path(str(source)+'-wal')
            if source.suffix=='.db' and not history:
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)) as src,closing(sqlite3.connect(target)) as dest:
                    src.backup(dest,pages=1024,sleep=.05)
                method='SQLITE_CONSISTENT_BACKUP'
            else:
                if history and wal.exists() and wal.stat().st_size:raise ValueError('BACKUP_RAW_HISTORY_HAS_ACTIVE_WAL_RETRY_LATER')
                before=sha(source);shutil.copyfile(source,target)
                if sha(source)!=before or sha(target)!=before:raise ValueError('BACKUP_SOURCE_CHANGED_RETRY')
                method='BYTE_EXACT_COPY'
            check_live_credentials(target,protected)
            entries.append(dict(path=rel.as_posix(),bytes=target.stat().st_size,sha256=sha(target),method=method))
        manifest=dict(format=FORMAT,created_at_utc=datetime.now(timezone.utc).isoformat(),
            scope='DNSE_REST_QUOTES_TRADES_WS_RAW_RULES_EVIDENCE_REVIEWS_AND_PROJECT_SOURCE',files=entries,
            excluded=['.env and credentials','large normalized.db and regenerable processed/exports','legacy Vnstock database','browser/test caches','GitHub state encryption key'],
            cloud_uploaded=False,live_credentials_scan='PASSED_EXACT_MATCH_CURRENT_ENV_VALUES_LENGTH_AT_LEAST_8',normalization_rule='data/control/dnse_normalization_rules.json',
            source_status='Raw checkpoints captured; availability/completion recorded inside each database. Old audit is historical, not an audit of newly collected days.')
        atomic(stage/'RECOVERY_MANIFEST.json',manifest)
        bundle=folder/'liquiditylab-recovery.tar.gz'
        with tarfile.open(bundle,'w:gz',compresslevel=6) as tar:
            for p in sorted(stage.rglob('*')):
                if p.is_file():tar.add(p,arcname=p.relative_to(stage).as_posix(),recursive=False)
        result=dict(state='CREATED_NOT_YET_RESTORE_VERIFIED',bundle=bundle.relative_to(root).as_posix(),
            bytes=bundle.stat().st_size,sha256=sha(bundle),files=len(entries),uncompressed_bytes=sum(e['bytes'] for e in entries),
            credentials_included=False,originals_deleted=False,cloud_uploaded=False)
        atomic(folder/'backup_result.json',result);atomic(root/'report/recovery_backup_latest.json',result)
        return result


def restore(bundle,destination):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    # Never extract over the active workspace or follow archive links.
    with tarfile.open(bundle,'r:gz') as tar:
        members=tar.getmembers();names=set();size=0
        for m in members:
            safe_path(destination,m.name)
            if not m.isfile() or m.name.casefold() in names:raise ValueError('BACKUP_LINK_OR_DUPLICATE_MEMBER')
            names.add(m.name.casefold());size+=m.size
            if m.size<0 or size>10*1024**3:raise ValueError('BACKUP_RESTORE_SIZE_LIMIT')
        for m in members:
            target=safe_path(destination,m.name);target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src,target.open('xb') as dest:shutil.copyfileobj(src,dest,1024*1024)
    manifest=json.loads((destination/'RECOVERY_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest['format']!=FORMAT:raise ValueError('BACKUP_FORMAT_INVALID')
    expected={'RECOVERY_MANIFEST.json',*(e['path'] for e in manifest['files'])}
    if {m.name for m in members}!=expected:raise ValueError('BACKUP_MANIFEST_MEMBERS_MISMATCH')
    sqlite_count=0
    for e in manifest['files']:
        p=safe_path(destination,e['path'])
        if p.stat().st_size!=e['bytes'] or sha(p)!=e['sha256']:raise ValueError('BACKUP_RESTORED_CHECKSUM_MISMATCH')
        if p.suffix=='.db':
            with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('BACKUP_SQLITE_INTEGRITY_FAILED')
            sqlite_count+=1
    return dict(state='RESTORED_FILES_CHECKSUM_AND_SQLITE_VERIFIED',files=len(manifest['files']),sqlite_databases=sqlite_count,
                bytes=sum(e['bytes'] for e in manifest['files']),destination=str(destination),full_canonical_rebuild_verified=False)


def verify_sample_rebuild(root,max_pages=12):
    from scripts.normalize_dnse_history import run
    from scripts.audit_dnse_normalized import check_record
    root=Path(root).resolve()
    result=run(root=root,max_pages=max_pages,database_path=root/'data/processed/dnse/normalized.db')
    if result['quarantined_occurrences'] or not result['unique_normalized_snapshots']:raise ValueError('BACKUP_REBUILD_SAMPLE_FAILED')
    checked=0
    output=Path(root)/'data/processed/dnse/normalized.db'
    with closing(sqlite3.connect(output)) as db:
        db.row_factory=sqlite3.Row
        rules=db.execute('SELECT hash FROM normalization_rules').fetchone()[0]
        for archive in db.execute('SELECT * FROM archives').fetchall():
            with closing(sqlite3.connect((Path(root)/archive['path']).resolve().as_uri()+'?mode=ro',uri=True)) as source:
                source.row_factory=sqlite3.Row
                for pid, in db.execute('SELECT page_id FROM processed_pages WHERE archive_id=?',(archive['id'],)).fetchall():
                    page=source.execute('SELECT * FROM pages WHERE id=?',(pid,)).fetchone()
                    raw=gzip.decompress(page['payload_gzip'])
                    if hashlib.sha256(raw).hexdigest()!=page['sha256']:raise ValueError('BACKUP_RAW_PAGE_HASH_FAILED')
                    quotes=json.loads(raw)['quotes']
                    for r in db.execute('SELECT l.ordinal,s.payload_zlib FROM lineage l JOIN snapshots s ON l.snapshot_id=s.id WHERE l.archive_id=? AND l.page_id=?',(archive['id'],pid)):
                        import zlib
                        record=json.loads(zlib.decompress(r['payload_zlib']))
                        check_record(record,quotes[r['ordinal']],page['received_at'],rules);checked+=1
    return dict(state='RAW_TO_CANONICAL_SAMPLE_REBUILT_AND_INDEPENDENTLY_CHECKED',pages=result['pages_processed'],records=checked,
                rule_hash=rules,full_canonical_rebuild_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--restore',type=Path);parser.add_argument('--destination',type=Path);parser.add_argument('--sample-rebuild',action='store_true');args=parser.parse_args()
    if args.restore:
        if not args.destination:parser.error('--destination is required for restore')
        result=restore(args.restore,args.destination)
        if args.sample_rebuild:result['normalization_sample']=verify_sample_rebuild(args.destination)
        atomic(ROOT/'report/recovery_restore_latest.json',result)
    else:result=create()
    print(json.dumps(result,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
