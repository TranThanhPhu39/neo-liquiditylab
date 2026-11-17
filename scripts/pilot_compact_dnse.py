"""Lossless one-day Parquet/Zstd pilot and an independent SQLite restore drill."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.create_recovery_backup import sha
from scripts.normalize_dnse_history import connect_output

SQL_COLUMNS=['id','event_key','symbol','exchange','source_time','source_local_date','received_at','quality',
             'units_status','market_session','financial_eligible','bid_depth','ask_depth','aggregate_inconsistent']


def arrow_schema():
    import pyarrow as pa
    integer={'id','financial_eligible','bid_depth','ask_depth','aggregate_inconsistent'}
    return pa.schema([(k,pa.int64() if k in integer else pa.string()) for k in SQL_COLUMNS]+
                     [('record_json',pa.binary()),('source_timestamp_utc',pa.timestamp('us',tz='UTC'))])


def row_hash(row,raw):
    return hashlib.sha256(json.dumps([row[k] for k in SQL_COLUMNS],ensure_ascii=False,separators=(',',':')).encode()+b'\0'+raw).digest()


def lineage_digest(db,day=None):
    h=hashlib.sha256()
    query='SELECT l.archive_id,l.page_id,l.ordinal,l.snapshot_id FROM lineage l'
    if day:query+=' JOIN snapshots s ON s.id=l.snapshot_id WHERE s.source_local_date=?'
    query+=' ORDER BY l.archive_id,l.page_id,l.ordinal'
    for r in db.execute(query,(day,) if day else ()):
        h.update(json.dumps(list(r),separators=(',',':')).encode()+b'\n')
    return h.hexdigest()


def day_metadata(db,day):
    archives=[dict(r) for r in db.execute('SELECT * FROM archives') if json.loads(r['settings'])['date']==day]
    ids=[r['id'] for r in archives]
    if not ids:raise ValueError('COMPACT_ARCHIVE_METADATA_MISSING')
    marks=','.join('?' for _ in ids);metadata={'archives':archives}
    for table in ('raw_catalog','processed_pages','quarantine'):
        metadata[table]=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE archive_id IN ({marks})',ids)]
    metadata['normalization_rules']=[dict(r) for r in db.execute('SELECT * FROM normalization_rules')]
    metadata['manual_checks']=[dict(r) for r in db.execute('SELECT * FROM manual_checks WHERE source_date=?',(day,))]
    return metadata


def require_complete_day(db,day,root):
    metadata=day_metadata(db,day)
    for a in metadata['archives']:
        rows=db.execute('SELECT COALESCE(sum(raw_count),0),COALESCE(sum(converted_count+quarantined_count),0),count(*) FROM processed_pages WHERE archive_id=?',(a['id'],)).fetchone()
        if rows[0]!=a['raw_records'] or rows[1]!=a['raw_records']:raise ValueError('COMPACT_DAY_NORMALIZATION_INCOMPLETE')
        raw=Path(root)/a['path']
        if raw.exists():
            with closing(sqlite3.connect(raw.resolve().as_uri()+'?mode=ro',uri=True)) as source:
                if source.execute('SELECT count(*) FROM pages').fetchone()[0]!=rows[2]:raise ValueError('COMPACT_DAY_NORMALIZATION_INCOMPLETE')
    return metadata


def export_day(day,*,root=ROOT,database_path=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    day=datetime.fromisoformat(day).date().isoformat();root=Path(root).resolve()
    folder=root/'data/compact/dnse'/(day+'-'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True,exist_ok=False)
    writers={};rows=compressed=0;expected=hashlib.sha256();schema=arrow_schema()
    path=database_path or root/'data/processed/dnse/normalized.db'
    lineage_hash=hashlib.sha256()
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        metadata=require_complete_day(db,day,root)
        total=db.execute('SELECT COUNT(*) FROM snapshots WHERE source_local_date=?',(day,)).fetchone()[0]
        if not total:raise ValueError('COMPACT_DAY_HAS_NO_RECORDS')
        cursor=db.execute('SELECT * FROM snapshots WHERE source_local_date=? ORDER BY id',(day,))
        try:
            while True:
                batch=cursor.fetchmany(8192)
                if not batch:break
                groups={}
                for row in batch:
                    raw=zlib.decompress(row['payload_zlib']);record=json.loads(raw)
                    if record['event_id']!=row['event_key'] or record['local_date']!=day:raise ValueError('COMPACT_SOURCE_IDENTITY_MISMATCH')
                    value={k:row[k] for k in SQL_COLUMNS};value.update(record_json=raw,source_timestamp_utc=datetime.fromisoformat(row['source_time']))
                    group=hashlib.sha256(row['symbol'].encode()).digest()[0]%4
                    groups.setdefault(group,[]).append(value)
                    expected.update(row_hash(row,raw));rows+=1;compressed+=len(row['payload_zlib'])
                for group,values in groups.items():
                    if group not in writers:writers[group]=pq.ParquetWriter(folder/f'quotes-{group}.parquet',schema,compression='zstd',compression_level=6)
                    writers[group].write_table(pa.Table.from_pylist(values,schema=schema))
                if rows%81920==0:print(json.dumps(dict(state='COMPACTING',day=day,rows=rows,total=total)),flush=True)
        finally:
            for w in writers.values():w.close()
        atomic(folder/'sqlite_metadata.json',metadata)
        lineage_schema=pa.schema([('archive_id',pa.string()),('page_id',pa.int64()),('ordinal',pa.int64()),('snapshot_id',pa.int64())])
        lineage_count=0
        with pq.ParquetWriter(folder/'lineage.parquet',lineage_schema,compression='zstd',compression_level=6) as w:
            cursor=db.execute('''SELECT l.* FROM lineage l JOIN snapshots s ON s.id=l.snapshot_id
                                 WHERE s.source_local_date=? ORDER BY l.archive_id,l.page_id,l.ordinal''',(day,))
            while True:
                batch=cursor.fetchmany(8192)
                if not batch:break
                w.write_table(pa.Table.from_pylist([dict(r) for r in batch],schema=lineage_schema));lineage_count+=len(batch)
                for r in batch:lineage_hash.update(json.dumps(list(r),separators=(',',':')).encode()+b'\n')
    if rows!=total:raise ValueError('COMPACT_ROW_COUNT_MISMATCH')
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(folder.iterdir()) if p.is_file()]
    manifest=dict(format='liquiditylab-lossless-parquet-day-v1',day=day,snapshots=rows,lineage_rows=lineage_count,
        source_compressed_payload_bytes=compressed,canonical_snapshot_digest=expected.hexdigest(),lineage_digest=lineage_hash.hexdigest(),
        parquet_and_metadata_bytes=sum(f['bytes'] for f in files),files=files,
        fidelity='Exact canonical JSON bytes, SQLite columns and snapshot IDs; lineage and metadata retained',
        original_database_changed=False,production_reader_migrated=False)
    atomic(folder/'manifest.json',manifest)
    return folder,manifest


def restore_day(folder,destination):
    import pyarrow.parquet as pq
    folder=Path(folder).resolve();destination=Path(destination).resolve()
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest['format']!='liquiditylab-lossless-parquet-day-v1':raise ValueError('COMPACT_FORMAT_INVALID')
    for f in manifest['files']:
        if Path(f['path']).name!=f['path']:raise ValueError('COMPACT_UNSAFE_PATH')
        if sha(folder/f['path'])!=f['sha256']:raise ValueError('COMPACT_CHECKSUM_MISMATCH')
    if destination.exists():raise ValueError('COMPACT_RESTORE_DESTINATION_EXISTS')
    metadata=json.loads((folder/'sqlite_metadata.json').read_text(encoding='utf-8'))
    count=lineage_count=0
    with closing(connect_output(destination)) as db:
        for table in ('normalization_rules','archives','raw_catalog','processed_pages','quarantine','manual_checks'):
            for r in metadata[table]:
                keys=list(r);db.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES({",".join("?" for _ in keys)})',[r[k] for k in keys])
        db.commit()
        for path in sorted(folder.glob('quotes-*.parquet')):
            for batch in pq.ParquetFile(path).iter_batches(batch_size=4096):
                records=batch.to_pylist();values=[]
                for r in records:
                    if datetime.fromisoformat(r['source_time'])!=r['source_timestamp_utc']:raise ValueError('COMPACT_TIMESTAMP_MISMATCH')
                    values.append([r[k] for k in SQL_COLUMNS]+[zlib.compress(r['record_json'],1)])
                with db:db.executemany('INSERT INTO snapshots ('+','.join(SQL_COLUMNS)+',payload_zlib) VALUES('+','.join('?' for _ in range(len(SQL_COLUMNS)+1))+')',values)
                count+=len(records)
        for batch in pq.ParquetFile(folder/'lineage.parquet').iter_batches(batch_size=8192):
            with db:db.executemany('INSERT INTO lineage VALUES(?,?,?,?)',[(r['archive_id'],r['page_id'],r['ordinal'],r['snapshot_id']) for r in batch.to_pylist()])
            lineage_count+=batch.num_rows
        actual=hashlib.sha256()
        for r in db.execute('SELECT * FROM snapshots ORDER BY id'):actual.update(row_hash(r,zlib.decompress(r['payload_zlib'])))
        if count!=manifest['snapshots'] or lineage_count!=manifest['lineage_rows'] or actual.hexdigest()!=manifest['canonical_snapshot_digest']:
            raise ValueError('COMPACT_LOSSLESS_ROUNDTRIP_FAILED')
        if manifest.get('lineage_digest') and lineage_digest(db)!=manifest['lineage_digest']:raise ValueError('COMPACT_LINEAGE_MAPPING_CHANGED')
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('COMPACT_RESTORED_DATABASE_INVALID')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return dict(state='EXACT_SNAPSHOT_AND_LINEAGE_ROUNDTRIP_VERIFIED',day=manifest['day'],snapshots=count,lineage_rows=lineage_count,
                canonical_snapshot_digest=actual.hexdigest(),restored_database=str(destination),restored_database_bytes=destination.stat().st_size,
                compact_bytes=manifest['parquet_and_metadata_bytes'],production_reader_migrated=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--date',default='2026-11-11');args=parser.parse_args()
    folder,manifest=export_day(args.date)
    result=restore_day(folder,folder/'restore-drill/normalized.db');result['folder']=folder.relative_to(ROOT).as_posix()
    atomic(ROOT/'report/storage_compaction_pilot.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':main()
