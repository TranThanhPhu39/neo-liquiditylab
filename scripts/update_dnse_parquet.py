"""Normalize/audit one source day in a disposable SQLite workspace, retain Parquet."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from scripts import normalize_dnse_history as normalizer, audit_dnse_normalized as auditor
from scripts.build_orderbook_monitor import atomic
from scripts.compact_dnse_history import verify_day
from scripts.pilot_compact_dnse import export_day, restore_day
from scripts.scheduled_collection import exclusive_job
from src.data_pipeline.parquet_history import ParquetHistory, digest, checked_folder


def audit_sources(root, entry, previous):
    folder=Path(root)/entry['folder']
    current=folder/'raw_audit.json'
    report=json.loads(current.read_bytes()) if current.exists() else previous
    if report.get('state')!='VERIFIED':raise ValueError('PARQUET_RAW_AUDIT_REQUIRED')
    verified={a['path']:a['sha256'] for a in report.get('source_archives_unchanged',[])}
    metadata=json.loads((folder/'sqlite_metadata.json').read_bytes())
    for archive in metadata['archives']:
        if verified.get(archive['path'])!=archive['sha256'] or digest(Path(root)/archive['path'])!=archive['sha256']:
            raise ValueError('PARQUET_RAW_SOURCE_NOT_AUDITED_OR_CHANGED')
    return metadata


def run(day, *, root, progress=lambda step: None):
    root=Path(root).resolve();catalog=root/'data/compact/catalog.json'
    # A second caller cannot publish a different catalog while this job builds a day.
    with exclusive_job(root/'data/control/dnse-compaction.lock'):
        index=json.loads(catalog.read_bytes())
        old_audit=json.loads((root/'report/dnse_normalization_audit.json').read_bytes())
        existing=next((e for e in index['quote_days'] if e['day']==day),None)
        # Resume only this day; never replay every historical raw file into a new DB.
        work=root/'data/processed/dnse/work'/day/'normalized.db'
        work.parent.mkdir(parents=True,exist_ok=True)
        if existing and not work.exists():
            checked_folder(root,existing)
            restore_day(root/existing['folder'],work)
        normalized=normalizer.run(root=root,days=[day],database_path=work,
                                  report_path=work.parent/'normalization.json')
        if normalized['state']!='COMPLETED' or normalized['quarantined_occurrences']:
            raise ValueError('NORMALIZATION_REQUIRES_REVIEW')
        progress('normalize')
        audited=auditor.audit(root=root,export=False,database_path=work,report_path=work.parent/'audit.json')
        if audited['state']!='VERIFIED':raise ValueError('NORMALIZED_AUDIT_NOT_VERIFIED')
        progress('audit_normalized')
        finance=auditor.validate_finance(root=root,database_path=work,report_path=work.parent/'finance.json')
        if finance['state']!='PASSED':raise ValueError('FINANCE_CHECKS_FAILED')
        progress('finance_checks')
        folder,_=export_day(day,root=root,database_path=work)
        entry=verify_day(folder,root=root,database_path=work)
        # Retain the independent raw reconciliation with the immutable day bundle.
        atomic(folder/'raw_audit.json',audited)
        atomic(folder/'finance_checks.json',finance)
        atomic(folder/'preceding_raw_audit.json',old_audit)
        manifest=json.loads((folder/'manifest.json').read_bytes())
        for name in ('raw_audit.json','finance_checks.json','preceding_raw_audit.json'):
            path=folder/name
            manifest['files'].append(dict(path=name,bytes=path.stat().st_size,sha256=digest(path)))
        atomic(folder/'manifest.json',manifest)
        entry['raw_audit']='raw_audit.json'
        entries=[e for e in index['quote_days'] if e['day']!=day]+[entry]
        entries.sort(key=lambda e:e['day'])
        all_metadata=[audit_sources(root,e,old_audit) for e in entries]
        sources={a['path']:dict(path=a['path'],sha256=a['sha256']) for m in all_metadata for a in m['archives']}
        raw=sum(a['raw_records'] for m in all_metadata for a in m['archives'])
        unique=sum(e['snapshots'] for e in entries)
        lineage=sum(e['lineage_rows'] for e in entries)
        if raw!=lineage or any(m['quarantine'] for m in all_metadata):raise ValueError('PARQUET_GLOBAL_RECONCILIATION_FAILED')
        # Keep global unit counts without reading the large record_json column.
        import pyarrow.dataset as ds
        units=Counter()
        for e in entries:
            for path in (root/e['folder']).glob('quotes-*.parquet'):
                for row in ds.dataset(path,format='parquet').to_table(columns=['units_status']).group_by('units_status').aggregate([('units_status','count')]).to_pylist():
                    units[row['units_status']]+=row['units_status_count']
        index['quote_days']=entries
        atomic(catalog,index)
        global_audit=dict(state='VERIFIED',storage='PARQUET',checked_at_utc=datetime.now(timezone.utc).isoformat(),
            raw_rows_reconciled=raw,unique_normalized_snapshots=unique,quarantined_rows=0,
            duplicates_preserved_as_lineage=lineage-unique,source_archives_unchanged=list(sources.values()),
            database=dict(path=catalog.relative_to(root).as_posix(),bytes=catalog.stat().st_size,sha256=digest(catalog)),
            independent_raw_audits='Per-day raw_audit.json or the preceding independently verified audit')
        normalized.update(raw_occurrences_processed=raw,unique_normalized_snapshots=unique,
                          duplicate_raw_occurrences=lineage-unique,unit_scope_counts=dict(units))
        atomic(root/'report/dnse_normalization_audit.json',global_audit)
        # Closed connections + checkpoint before deleting this one-day scratch DB.
        with closing(sqlite3.connect(work)) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if work.resolve().parent!=root/'data/processed/dnse/work'/day:raise ValueError('PARQUET_UNSAFE_WORK_PATH')
        for path in (work,Path(str(work)+'-wal'),Path(str(work)+'-shm')):
            if path.exists():path.unlink()
        return normalized,global_audit,finance
