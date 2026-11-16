"""Scope the old normalized DB precisely before considering derived-file removal."""
from contextlib import ExitStack,closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3

from config.settings import ROOT
from scripts.recovery_coverage import verified_recovery_files
from scripts.compact_dnse_history import verify_day
from scripts.scheduled_collection import exclusive_job
from scripts.build_orderbook_monitor import atomic
from src.data_pipeline.parquet_history import checked_folder,digest


def verify_metadata(root,source,days):
    root=Path(root);catalog=json.loads((root/'data/compact/catalog.json').read_bytes())
    mapping={e['day']:e for e in catalog['quote_days']}
    tables={'archives','raw_catalog','processed_pages','quarantine','normalization_rules','manual_checks'}
    with closing(sqlite3.connect(Path(source).as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        actual_tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if actual_tables!=tables|{'snapshots','lineage'}:raise ValueError('LEGACY_UNARCHIVED_TABLE_PRESENT')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():raise ValueError('LEGACY_CUSTOM_SCHEMA_PRESENT')
        for table in tables:
            expected=set()
            for day in days:
                rows=json.loads((root/mapping[day]['folder']/'sqlite_metadata.json').read_bytes())[table]
                expected.update(json.dumps(r,sort_keys=True,separators=(',',':')) for r in rows)
            actual={json.dumps(dict(r),sort_keys=True,separators=(',',':')) for r in db.execute('SELECT * FROM '+table)}
            if expected!=actual:raise ValueError('LEGACY_UNARCHIVED_METADATA_PRESENT')
    return sorted(tables)


def audit(root=ROOT):
    root=Path(root).resolve();source=root/'data/processed/dnse/normalized.db'
    if not source.is_file():return dict(state='LEGACY_DATABASE_ALREADY_ABSENT')
    before=(source.stat().st_size,source.stat().st_mtime_ns)
    with ExitStack() as locks:
        for name in ('dnse_update','dnse_history','dnse_normalization','dnse-compaction'):
            locks.enter_context(exclusive_job(root/'data/control'/(name+'.lock')))
        catalog=json.loads((root/'data/compact/catalog.json').read_bytes())
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            days=dict(db.execute('SELECT source_local_date,count(*) FROM snapshots GROUP BY source_local_date'))
            lineage=db.execute('SELECT count(*) FROM lineage').fetchone()[0]
        mapping={e['day']:e for e in catalog['quote_days']};verified=[]
        required={}
        for day in days:
            folder,manifest=checked_folder(root,mapping[day])
            required.update({(folder/f['path']).relative_to(root).as_posix():f['sha256'] for f in manifest['files']})
        saved=verified_recovery_files(root,required)
        for day,count in sorted(days.items()):
            entry=mapping.get(day)
            if not entry or entry.get('state')!='SOURCE_AND_PARQUET_ALL_ROWS_VERIFIED' or entry['snapshots']!=count:
                raise ValueError('LEGACY_DAY_REPLACEMENT_MISSING')
            folder,manifest=checked_folder(root,entry)
            for item in manifest['files']:
                relative=(folder/item['path']).relative_to(root).as_posix()
                if saved.get(relative)!=item['sha256']:raise ValueError('LEGACY_REPLACEMENT_NOT_IN_RESTORED_BACKUP')
            proof=verify_day(folder,root=root,database_path=source)
            verified.append(proof)
            print(json.dumps(dict(state='LEGACY_DAY_ALL_ROWS_VERIFIED',day=day,rows=count)),flush=True)
        if sum(p['lineage_rows'] for p in verified)!=lineage:raise ValueError('LEGACY_LINEAGE_COVERAGE_MISMATCH')
        metadata_tables={'archives','raw_catalog','processed_pages','quarantine','normalization_rules','manual_checks'}
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            tables={r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables!=metadata_tables|{'snapshots','lineage'}:raise ValueError('LEGACY_UNARCHIVED_TABLE_PRESENT')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
                raise ValueError('LEGACY_CUSTOM_SCHEMA_PRESENT')
            for table in metadata_tables:
                expected=set()
                for day in days:
                    folder=root/mapping[day]['folder']
                    rows=json.loads((folder/'sqlite_metadata.json').read_bytes())[table]
                    expected.update(json.dumps(r,sort_keys=True,separators=(',',':')) for r in rows)
                actual={json.dumps(dict(r),sort_keys=True,separators=(',',':')) for r in db.execute('SELECT * FROM '+table)}
                if expected!=actual:raise ValueError('LEGACY_UNARCHIVED_METADATA_PRESENT')
        if before!=(source.stat().st_size,source.stat().st_mtime_ns):raise ValueError('LEGACY_SQLITE_CHANGED_DURING_AUDIT')
        result=dict(state='LEGACY_SQLITE_ALL_ROWS_AND_RESTORE_COVERAGE_VERIFIED',path=source.relative_to(root).as_posix(),
            bytes=before[0],sha256=digest(source),days=verified,snapshots=sum(days.values()),lineage_rows=lineage,
            checked_at_utc=datetime.now(timezone.utc).isoformat(),raw_originals_preserved=True,deleted=False,metadata_tables_verified=sorted(metadata_tables),
            basis='All legacy days match independently verified Parquet and a previously fully restored backup; newer catalog days do not need to exist in this old DB.')
        atomic(root/'report/legacy_sqlite_removal_eligibility.json',result)
        return result


if __name__=='__main__':
    r=audit();print(json.dumps({k:v for k,v in r.items() if k!='days'}))
