"""Migrate replay payloads and verify existing dashboard queries against Parquet."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from config.settings import ROOT
from scripts.build_orderbook_monitor import atomic
from scripts.scheduled_collection import exclusive_job
from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.parquet_history import ParquetHistory, checked_folder, digest
from src.data_pipeline.parquet_replay import write_pack, read_pack


def migrate(root=ROOT):
    root=Path(root).resolve()
    store=ParquetHistory(root/'data/compact/catalog.json')
    legacy=DNSEHistory(root/'data/processed/dnse/normalized.db',prefer_parquet=False)
    result=dict(state='VERIFYING',production_reader_migrated=True,cloud_uploaded=False,
                query_cases=0,replays=[],trades=[],replaced_files=[])
    index_path=root/'data/exports/orderbook-monitor/index.json'
    with exclusive_job(root/'data/control/orderbook-monitor-build.lock'):
        index=json.loads(index_path.read_bytes())
        for entry in index['datasets']:
            path=index_path.parent/entry['file']
            if path.exists():
                original=json.loads(path.read_bytes())
                relative=write_pack(root,original)
                if read_pack(root,relative)!=original:raise ValueError('MIGRATION_REPLAY_MISMATCH')
                entry.update(storage='PARQUET',compact_folder=relative)
                result['replaced_files'].append(dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=digest(path)))
            elif not entry.get('compact_folder'):
                raise ValueError('MIGRATION_REPLAY_SOURCE_MISSING')
            package=read_pack(root,entry['compact_folder'])
            if package['dataset_id']!=entry['dataset_id']:raise ValueError('MIGRATION_REPLAY_IDENTITY_MISMATCH')
            result['replays'].append(dict(dataset_id=entry['dataset_id'],frames=len(package['frames']),folder=entry['compact_folder']))
            print(json.dumps(dict(state='REPLAY_VERIFIED',day=entry['day'],symbol=entry['symbol'])),flush=True)
        for day in store.dates():
            checked_folder(root,store.days[day])
            if legacy.catalog(day)!=store.catalog(day):raise ValueError('MIGRATION_RAW_CATALOG_MISMATCH')
            for symbol in ('ACB','FPT','HPG'):
                if legacy.window(symbol,day)!=store.window(symbol,day):raise ValueError('MIGRATION_WINDOW_MISMATCH')
                if legacy.records(symbol,day)!=store.records(symbol,day):raise ValueError('MIGRATION_RECORDS_MISMATCH')
                for clock in ('00:00:00','11:29:59.999999','14:20:59.999999','23:59:59.999999'):
                    at=datetime.fromisoformat(day+'T'+clock+'+07:00')
                    if legacy.reconstruct_at([symbol],day,at)!=store.reconstruct_at([symbol],day,at):
                        raise ValueError('MIGRATION_CUTOFF_MISMATCH')
                    if legacy.sampled_records(symbol,day,at,limit=240)!=store.sampled_records(symbol,day,at,limit=240):
                        raise ValueError('MIGRATION_SAMPLE_MISMATCH')
                    result['query_cases']+=1
            print(json.dumps(dict(state='DASHBOARD_QUERIES_VERIFIED',day=day)),flush=True)
        for entry in store.index.get('trade_packages',[]):
            checked_folder(root,entry)
            package=store.trades(entry['symbol'],entry['day'])
            path=root/'data/exports/orderbook-trades'/f'dnse-{entry["symbol"].lower()}-{entry["day"]}.json'
            if path.exists():
                if package!=json.loads(path.read_bytes()):raise ValueError('MIGRATION_TRADE_MISMATCH')
                result['replaced_files'].append(dict(path=path.relative_to(root).as_posix(),bytes=path.stat().st_size,sha256=digest(path)))
            result['trades'].append(dict(symbol=entry['symbol'],day=entry['day'],trades=len(package['trades'])))
        # Readers switch only after every replay and trade package passed round-trip.
        atomic(index_path,index)
        result.update(state='DASHBOARD_PARQUET_VERIFIED',checked_at=datetime.now().astimezone().isoformat())
        atomic(root/'report/dashboard_parquet_migration.json',result)
    return result


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    r=migrate()
    print(json.dumps(dict(state=r['state'],query_cases=r['query_cases'],replays=len(r['replays']),trades=len(r['trades']))))


if __name__=='__main__':main()
