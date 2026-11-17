"""Delete only verified, replaced dashboard files; defer an active SQLite writer."""
import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tarfile
import time

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.scheduled_collection import exclusive_job
from src.data_pipeline.parquet_history import checked_folder, confined, digest


def backup_verified(root):
    proof=json.loads((root/'report/dashboard_parquet_backup.json').read_bytes())
    if proof['state']!='FULL_BACKUP_RESTORE_VERIFIED':raise ValueError('PARQUET_BACKUP_RESTORE_REQUIRED')
    bundle=confined(root,proof['bundle'])
    if digest(bundle)!=proof['sha256']:raise ValueError('PARQUET_BACKUP_CHANGED')
    manifest=None
    with tarfile.open(bundle,'r|gz') as archive:
        for member in archive:
            if member.name=='RECOVERY_MANIFEST.json':
                manifest=json.load(archive.extractfile(member));break
    if manifest is None:raise ValueError('PARQUET_BACKUP_MANIFEST_MISSING')
    return {e['path']:e['sha256'] for e in manifest['files']}


def run(root=ROOT):
    root=Path(root).resolve()
    report=root/'report/dashboard_parquet_cleanup.json'
    previous=json.loads(report.read_bytes()) if report.exists() else {}
    result=dict(state='VERIFYING',deleted_files=previous.get('deleted_files',[]),pending=[],cloud_uploaded=False)
    migration=json.loads((root/'report/dashboard_parquet_migration.json').read_bytes())
    if migration['state']!='DASHBOARD_PARQUET_VERIFIED':raise ValueError('PARQUET_MIGRATION_VERIFICATION_REQUIRED')
    saved=backup_verified(root)
    with ExitStack() as stack:
        for lock in ('orderbook-monitor-build','dnse-trades-build','dnse-compaction'):
            stack.enter_context(exclusive_job(root/'data/control'/(lock+'.lock')))
        index=json.loads((root/'data/exports/orderbook-monitor/index.json').read_bytes())
        catalog=json.loads((root/'data/compact/catalog.json').read_bytes())
        entries=[*catalog['quote_days'],*catalog.get('trade_packages',[]),
                 *(dict(folder=e['compact_folder']) for e in index['datasets'])]
        for entry in entries:
            folder,manifest=checked_folder(root,entry)
            for item in manifest['files']:
                path=folder/item['path']
                if saved.get(path.relative_to(root).as_posix())!=item['sha256']:
                    raise ValueError('PARQUET_REPLACEMENT_NOT_IN_VERIFIED_BACKUP')
        # Only file names recorded by the successful migration may be removed.
        for item in migration['replaced_files']:
            path=confined(root,item['path'])
            if path.parent not in (root/'data/exports/orderbook-monitor',root/'data/exports/orderbook-trades'):
                raise ValueError('PARQUET_UNSAFE_CLEANUP_TARGET')
            if path.exists():
                if path.stat().st_size!=item['bytes'] or digest(path)!=item['sha256']:
                    raise ValueError('PARQUET_OLD_FILE_CHANGED_RETRY_MIGRATION')
                path.unlink()
                result['deleted_files'].append(item)
                result['freed_bytes']=sum(e['bytes'] for e in result['deleted_files'])
                atomic(report,result)
        try:
            with ExitStack() as writers:
                for lock in ('dnse_update','dnse_history','dnse_normalization'):
                    writers.enter_context(exclusive_job(root/'data/control'/(lock+'.lock')))
                source=root/'data/processed/dnse/normalized.db'
                if source.exists():
                    audit=json.loads((root/'report/dnse_normalization_audit.json').read_bytes())
                    snapshots=sum(e['snapshots'] for e in catalog['quote_days'])
                    lineage=sum(e['lineage_rows'] for e in catalog['quote_days'])
                    if audit.get('state')!='VERIFIED' or audit.get('raw_rows_reconciled')!=lineage or audit.get('unique_normalized_snapshots')!=snapshots:
                        result['pending'].append('SQLITE_WAITING_FOR_FINAL_RAW_AUDIT')
                    else:
                        from scripts.compact_dnse_history import verify_day
                        for entry in catalog['quote_days']:
                            verify_day(root/entry['folder'],root=root)
                        with closing(sqlite3.connect(source)) as db:
                            if db.execute('SELECT count(*) FROM snapshots').fetchone()[0]!=snapshots or db.execute('SELECT count(*) FROM lineage').fetchone()[0]!=lineage:
                                raise ValueError('PARQUET_SQLITE_CHANGED_AFTER_COMPACTION')
                            actual=dict(db.execute('SELECT source_local_date,count(*) FROM snapshots GROUP BY source_local_date'))
                            if actual!={e['day']:e['snapshots'] for e in catalog['quote_days']}:
                                raise ValueError('PARQUET_SQLITE_DAY_COUNTS_CHANGED')
                            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]!=0:
                                raise RuntimeError('PARQUET_SQLITE_STILL_BUSY')
                        manifest_path=root/'data/processed/dnse/manifest.json'
                        manifest=json.loads(manifest_path.read_bytes())
                        atomic(root/'data/compact/legacy_sqlite_manifest.json',manifest)
                        for path in (source,Path(str(source)+'-wal'),Path(str(source)+'-shm'),source.parent/'quotes.normalized.jsonl.gz'):
                            if path.exists():
                                item=dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=digest(path))
                                path.unlink();result['deleted_files'].append(item)
                        manifest.update(storage='PARQUET',exports_refreshed=False)
                        manifest['files']={'database':dict(path='data/compact/catalog.json',bytes=(root/'data/compact/catalog.json').stat().st_size,
                                                           sha256=digest(root/'data/compact/catalog.json'))}
                        atomic(manifest_path,manifest)
        except RuntimeError as error:
            if str(error) not in ('ANOTHER_COLLECTION_JOB_IS_RUNNING','PARQUET_SQLITE_STILL_BUSY'):raise
            result['pending'].append('SQLITE_IN_USE_BY_EXISTING_COLLECTION_OR_NORMALIZATION')
    result.update(state='CLEANED' if not result['pending'] else 'JSON_CLEANED_SQLITE_DEFERRED',
                  freed_bytes=sum(e['bytes'] for e in result['deleted_files']),
                  checked_at_utc=datetime.now(timezone.utc).isoformat())
    atomic(report,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-seconds',type=int,default=0)
    args=parser.parse_args();deadline=time.monotonic()+max(0,args.wait_seconds)
    while True:
        existing=ROOT/'report/dashboard_parquet_cleanup.json'
        if args.wait_seconds and existing.exists():
            prior=json.loads(existing.read_bytes())
            if prior.get('pending'):
                try:
                    with exclusive_job(ROOT/'data/control/dnse_update.lock'):pass
                except RuntimeError:
                    if time.monotonic()>=deadline:
                        result=prior;break
                    time.sleep(min(30,max(0,deadline-time.monotonic())))
                    continue
        result=run()
        print(json.dumps({k:v for k,v in result.items() if k!='deleted_files'}),flush=True)
        if not result['pending'] or time.monotonic()>=deadline:break
        time.sleep(min(30,max(0,deadline-time.monotonic())))
    return 0 if not result['pending'] else 2


if __name__=='__main__':raise SystemExit(main())
