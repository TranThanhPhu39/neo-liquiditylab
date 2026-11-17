"""Compact every complete canonical day and trade package, with lossless verification."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import heapq
import json
from pathlib import Path
import sqlite3
import time
import zlib

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.create_recovery_backup import sha
from scripts.pilot_compact_dnse import export_day,row_hash,lineage_digest,day_metadata,require_complete_day
from scripts.scheduled_collection import exclusive_job


def json_bytes(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def metadata_digest(value):
    canonical={table:sorted(json_bytes(row).decode() for row in rows) for table,rows in value.items()}
    return hashlib.sha256(json_bytes(canonical)).hexdigest()


def checked_manifest(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    for f in manifest['files']:
        if Path(f['path']).name!=f['path'] or (folder/f['path']).is_symlink():raise ValueError('COMPACT_UNSAFE_PATH')
        if (folder/f['path']).stat().st_size!=f['bytes'] or sha(folder/f['path'])!=f['sha256']:raise ValueError('COMPACT_CHECKSUM_MISMATCH')
    return manifest


def quote_rows(path):
    import pyarrow.parquet as pq
    previous=-1
    for batch in pq.ParquetFile(path).iter_batches(batch_size=2048):
        for r in batch.to_pylist():
            if r['id']<=previous:raise ValueError('COMPACT_QUOTE_ID_ORDER_INVALID')
            previous=r['id'];yield r


def verify_day(folder,*,root=ROOT,database_path=None):
    import pyarrow.parquet as pq
    folder=Path(folder);root=Path(root);manifest=checked_manifest(folder);day=manifest['day']
    if manifest['format']!='liquiditylab-lossless-parquet-day-v1':raise ValueError('COMPACT_FORMAT_INVALID')
    stored=hashlib.sha256();count=0;previous=-1
    for r in heapq.merge(*(quote_rows(p) for p in sorted(folder.glob('quotes-*.parquet'))),key=lambda r:r['id']):
        if r['id']<=previous or r['source_local_date']!=day:raise ValueError('COMPACT_DUPLICATE_OR_WRONG_DAY')
        if datetime.fromisoformat(r['source_time'])!=r['source_timestamp_utc']:raise ValueError('COMPACT_TIMESTAMP_MISMATCH')
        previous=r['id'];stored.update(row_hash(r,r['record_json']));count+=1
    lineage=hashlib.sha256();lineage_count=0;last=None
    for batch in pq.ParquetFile(folder/'lineage.parquet').iter_batches(batch_size=8192):
        for r in batch.to_pylist():
            values=[r[k] for k in ('archive_id','page_id','ordinal','snapshot_id')]
            if last is not None and tuple(values[:3])<=last:raise ValueError('COMPACT_LINEAGE_ORDER_INVALID')
            last=tuple(values[:3]);lineage.update(json.dumps(values,separators=(',',':')).encode()+b'\n');lineage_count+=1
    if count!=manifest['snapshots'] or stored.hexdigest()!=manifest['canonical_snapshot_digest'] or lineage_count!=manifest['lineage_rows']:
        raise ValueError('COMPACT_STORED_DIGEST_MISMATCH')
    if manifest.get('lineage_digest') and lineage.hexdigest()!=manifest['lineage_digest']:raise ValueError('COMPACT_LINEAGE_MAPPING_CHANGED')
    # Independent comparison to the actual database, not only the writer's manifest.
    with closing(sqlite3.connect((database_path or root/'data/processed/dnse/normalized.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN');metadata=require_complete_day(db,day,root)
        source=hashlib.sha256();source_rows=0
        for r in db.execute('SELECT * FROM snapshots WHERE source_local_date=? ORDER BY id',(day,)):
            source.update(row_hash(r,zlib.decompress(r['payload_zlib'])));source_rows+=1
        if source_rows!=count or source.hexdigest()!=stored.hexdigest():raise ValueError('COMPACT_SOURCE_SNAPSHOT_MISMATCH')
        if lineage_digest(db,day)!=lineage.hexdigest():raise ValueError('COMPACT_SOURCE_LINEAGE_MISMATCH')
        if metadata_digest(metadata)!=metadata_digest(json.loads((folder/'sqlite_metadata.json').read_text(encoding='utf-8'))):raise ValueError('COMPACT_SOURCE_METADATA_MISMATCH')
    result=dict(state='SOURCE_AND_PARQUET_ALL_ROWS_VERIFIED',day=day,snapshots=count,lineage_rows=lineage_count,
        canonical_snapshot_digest=stored.hexdigest(),lineage_digest=lineage.hexdigest(),
        bytes=manifest['parquet_and_metadata_bytes'],folder=folder.relative_to(root).as_posix(),
        verified_at_utc=datetime.now(timezone.utc).isoformat())
    atomic(folder/'verification.json',result);return result


def compact_trades(source,*,root=ROOT,output=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    source=Path(source);root=Path(root);before=sha(source);package=json.loads(source.read_text(encoding='utf-8'))
    if package.get('format')!='liquiditylab-market-trades-v1':raise ValueError('COMPACT_TRADE_FORMAT_INVALID')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    folder=Path(output) if output else root/'data/compact/dnse-trades'/(package['day']+'-'+package['symbol'].lower()+'-'+stamp)
    folder.mkdir(parents=True,exist_ok=False)
    schema=pa.schema([('id',pa.string()),('time',pa.timestamp('us',tz='UTC')),('price',pa.string()),('quantity',pa.int64()),('side',pa.string()),('record_json',pa.binary())])
    with pq.ParquetWriter(folder/'trades.parquet',schema,compression='zstd',compression_level=6) as writer:
        for start in range(0,len(package['trades']),8192):
            rows=[dict(id=t['id'],time=datetime.fromisoformat(t['time']),price=t['price'],quantity=t['qty'],side=t['side'],record_json=json_bytes(t)) for t in package['trades'][start:start+8192]]
            writer.write_table(pa.Table.from_pylist(rows,schema=schema))
    metadata={k:v for k,v in package.items() if k!='trades'};atomic(folder/'metadata.json',metadata)
    restored=[]
    for batch in pq.ParquetFile(folder/'trades.parquet').iter_batches(batch_size=2048):
        for r in batch.to_pylist():
            t=json.loads(r['record_json'])
            if (t['id']!=r['id'] or datetime.fromisoformat(t['time'])!=r['time'] or t['price']!=r['price'] or t['qty']!=r['quantity'] or t['side']!=r['side']):raise ValueError('COMPACT_TRADE_PROJECTION_MISMATCH')
            restored.append(t)
    reloaded=json.loads((folder/'metadata.json').read_text(encoding='utf-8'));reloaded['trades']=restored
    if json_bytes(reloaded)!=json_bytes(package) or sha(source)!=before:raise ValueError('COMPACT_TRADE_LOSSLESS_CHECK_FAILED')
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(folder.iterdir())]
    result=dict(format='liquiditylab-lossless-parquet-trades-v1',state='ALL_TRADE_FIELDS_AND_METADATA_VERIFIED',day=package['day'],symbol=package['symbol'],
        trades=len(restored),trade_dataset_id=package['trade_dataset_id'],unit_status=package['unit_status'],source_sha256=before,
        source_bytes=source.stat().st_size,bytes=sum(f['bytes'] for f in files),files=files,folder=folder.relative_to(root).as_posix())
    atomic(folder/'manifest.json',result);return result


def run(root=ROOT,days=None,wait_seconds=900):
    root=Path(root).resolve();report=root/'report/storage_compaction_all.json';catalog=root/'data/compact/catalog.json'
    with exclusive_job(root/'data/control/dnse-compaction.lock'):
        with closing(sqlite3.connect((root/'data/processed/dnse/normalized.db').as_uri()+'?mode=ro',uri=True)) as db:
            source_days=[r[0] for r in db.execute('SELECT DISTINCT source_local_date FROM snapshots ORDER BY source_local_date')]
        requested=days or source_days
        if any(d not in source_days for d in requested):raise ValueError('COMPACT_REQUESTED_DAY_NOT_FOUND')
        prior=json.loads(catalog.read_text(encoding='utf-8')) if catalog.exists() else {}
        quotes=[];trades=[];pending=[]
        def save(state):
            result=dict(state=state,requested_days=requested,quotes=quotes,trades=trades,pending_days=pending,
                quote_snapshots=sum(e['snapshots'] for e in quotes),lineage_rows=sum(e['lineage_rows'] for e in quotes),
                trade_records=sum(e['trades'] for e in trades),compact_bytes=sum(e['bytes'] for e in quotes+trades),
                original_database_deleted=False,production_reader_migrated=False,cloud_uploaded=False)
            quote_map={e['day']:e for e in prior.get('quote_days',[])}
            quote_map.update({e['day']:e for e in quotes})
            trade_map={(e['day'],e['symbol']):e for e in prior.get('trade_packages',[])}
            trade_map.update({(e['day'],e['symbol']):e for e in trades})
            atomic(report,result);atomic(catalog,dict(format='liquiditylab-parquet-catalog-v1',quote_days=sorted(quote_map.values(),key=lambda e:e['day']),trade_packages=sorted(trade_map.values(),key=lambda e:(e['day'],e['symbol']))));return result
        save('COMPACTING')
        for day in requested:
            try:
                with closing(sqlite3.connect((root/'data/processed/dnse/normalized.db').as_uri()+'?mode=ro',uri=True)) as db:
                    db.row_factory=sqlite3.Row;require_complete_day(db,day,root)
            except ValueError as e:
                if str(e)!='COMPACT_DAY_NORMALIZATION_INCOMPLETE':raise
                pending.append(day);save('COMPACTING_COMPLETE_DAYS');continue
            reused=None
            for m in sorted((root/'data/compact/dnse').glob(day+'-*/manifest.json'),reverse=True):
                try:reused=verify_day(m.parent,root=root);break
                except ValueError:continue
            if reused:quotes.append(reused)
            else:
                folder,_=export_day(day,root=root);quotes.append(verify_day(folder,root=root))
            save('COMPACTING');print(json.dumps(dict(state='DAY_VERIFIED',**{k:quotes[-1][k] for k in ('day','snapshots','lineage_rows','bytes')})),flush=True)
        for source in sorted((root/'data/exports/orderbook-trades').glob('dnse-*.json')):
            package=json.loads(source.read_text(encoding='utf-8'))
            if package['day'] not in requested:continue
            existing=None
            for m in (root/'data/compact/dnse-trades').glob(package['day']+'-'+package['symbol'].lower()+'-*/manifest.json'):
                value=checked_manifest(m.parent)
                if value.get('source_sha256')==sha(source):existing=value;break
            trades.append(existing or compact_trades(source,root=root));save('COMPACTING')
        deadline=time.monotonic()+wait_seconds
        while pending and time.monotonic()<=deadline:
            day=pending[0]
            try:
                with closing(sqlite3.connect((root/'data/processed/dnse/normalized.db').as_uri()+'?mode=ro',uri=True)) as db:
                    db.row_factory=sqlite3.Row;require_complete_day(db,day,root)
            except ValueError as e:
                if str(e)!='COMPACT_DAY_NORMALIZATION_INCOMPLETE':raise
                save('WAITING_FOR_NORMALIZATION');print(json.dumps(dict(state='WAITING_FOR_NORMALIZATION',day=day)),flush=True);time.sleep(10);continue
            folder,_=export_day(day,root=root);quotes.append(verify_day(folder,root=root));pending.pop(0);save('COMPACTING')
        result=save('ALL_AVAILABLE_DATA_VERIFIED' if not pending else 'COMPLETE_DAYS_VERIFIED_NORMALIZATION_PENDING')
        print(json.dumps({k:v for k,v in result.items() if k not in ('quotes','trades')}),flush=True);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);g=parser.add_mutually_exclusive_group(required=True);g.add_argument('--all-days',action='store_true');g.add_argument('--date');parser.add_argument('--wait-seconds',type=int,default=900);args=parser.parse_args()
    result=run(days=[args.date] if args.date else None,wait_seconds=args.wait_seconds)
    return 0 if not result['pending_days'] else 2


if __name__=='__main__':raise SystemExit(main())
