"""DNSE realtime runner with encrypted catalog bootstrap and schema-neutral restore."""
import argparse
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from config.settings import ROOT
from scripts.collect_dnse_realtime import session_plan
from scripts.github_checkpoint import ReleaseStore, backup_sqlite, state_key
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_realtime import collect, Limits, validate_catalog
from src.data_pipeline.dnse_realtime_store import audit
from src.models.orderbook import LOCAL_TZ

REPOSITORY = 'TranThanhPhu39/neo-liquiditylab'


def require_environment():
    if os.getenv('GITHUB_REPOSITORY') != REPOSITORY or os.getenv('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('WS_UNEXPECTED_REPOSITORY_OR_REF')
    if not all(os.getenv(k) for k in ('DNSE_API_KEY', 'DNSE_API_SECRET', 'LIQUIDITYLAB_STATE_KEY')):
        raise ValueError('WS_REQUIRED_SECRETS_MISSING')
    if os.getenv('DNSE_REALTIME_STORAGE_APPROVED') != 'true':
        raise ValueError('WS_ENCRYPTED_STORAGE_NOT_APPROVED')


def load_seed(key):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        tag = ReleaseStore(REPOSITORY, key, namespace='dnse-ws-catalog').restore('latest', folder/'catalog.db', folder/'reports')
        if not tag:
            raise ValueError('WS_ENCRYPTED_CATALOG_NOT_FOUND')
        with closing(sqlite3.connect((folder/'catalog.db').as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute("SELECT value FROM metadata WHERE key='catalog'").fetchone()
            if not row:
                raise ValueError('WS_CATALOG_PAYLOAD_MISSING')
            catalog = json.loads(row[0])
    validate_catalog(catalog, datetime.now(LOCAL_TZ).date())
    return catalog, tag


def run(mode='smoke', session='auto', seconds=60):
    require_environment()
    now = datetime.now(LOCAL_TZ)
    if os.getenv('GITHUB_EVENT_NAME') == 'schedule':
        scheduled = {'52 1 * * 1-5':'morning','52 5 * * 1-5':'afternoon'}
        if os.getenv('DNSE_SCHEDULE_CRON') not in scheduled:
            raise ValueError('WS_UNKNOWN_SCHEDULE')
        session = scheduled[os.environ['DNSE_SCHEDULE_CRON']]
    plan = session_plan(now, session)
    if mode == 'collect' and os.getenv('DNSE_REALTIME_ENABLED') != 'true':
        raise ValueError('WS_SCHEDULE_NOT_ENABLED')
    if mode == 'collect' and not plan['ready']:
        return {'state': 'SKIPPED_OUTSIDE_COLLECTION_WINDOW', 'purpose': 'REALTIME_G1_COLLECTOR', 'date_local': now.date().isoformat()}
    if mode == 'smoke' and not 1 <= seconds <= 120:
        raise ValueError('WS_SMOKE_DURATION_INVALID')
    key = state_key(os.environ['LIQUIDITYLAB_STATE_KEY'])
    catalog, seed = load_seed(key)
    partition = 'smoke' if mode == 'smoke' else plan['session']
    day = now.date().isoformat()
    day_key = day+'-'+partition
    folder = ROOT/'data/realtime/dnse'/day/partition
    folder.mkdir(parents=True, exist_ok=True)
    database = folder/'quotes.db'
    reports = ROOT/'report/realtime/dnse'/day/partition
    store = ReleaseStore(REPOSITORY, key, namespace='dnse-ws-state')
    restored = None
    if not database.exists():
        restored = store.restore(day_key, database, reports/'restored')
    write_json(reports/'universe.json', catalog)
    print(json.dumps({'purpose': 'REALTIME_G1_COLLECTOR', 'state': 'PREPARED', 'date_local': day,
                      'partition': partition, 'catalog_symbols': catalog['count'], 'catalog_as_of_date': catalog['as_of_date'],
                      'seed_checkpoint': seed, 'restored_checkpoint': restored}), flush=True)

    def status(value):
        write_json(reports/'status.json', value)
        # All collector fields are counters, clocks and internally-generated codes.
        print(json.dumps(value), flush=True)

    def checkpoint(path, value):
        with tempfile.TemporaryDirectory() as temp:
            frozen = Path(temp)
            backup_sqlite(path, frozen/'frozen.db')
            checked = audit(frozen/'frozen.db', verify_payloads=False)
            write_json(frozen/'status.json', {**value, 'snapshot_counts': checked})
            write_json(frozen/'audit.json', checked)
            write_json(frozen/'universe.json', catalog)
            tag = store.publish(day_key, frozen/'frozen.db', frozen,
                                os.getenv('GITHUB_RUN_ID', 'manual'), os.getenv('GITHUB_RUN_ATTEMPT', '1'))
        write_json(ROOT/'report/github/dnse_realtime_checkpoint.json', {'state': 'SAVED', 'tag': tag, 'partition': partition})

    async def execute():
        if mode == 'collect':
            wait = plan['start_delay_seconds']
            while wait > 0:
                if (ROOT/'data/control/STOP_DNSE_REALTIME').exists():
                    break
                await asyncio.sleep(min(1, wait))
                wait = max(0, (plan['start']-datetime.now(LOCAL_TZ)).total_seconds())
        duration = seconds if mode == 'smoke' else max(1, int((plan['end']-datetime.now(LOCAL_TZ)).total_seconds()))
        limits = Limits(checkpoint_seconds=30 if mode == 'smoke' else 900)
        return await collect(database, catalog, day, partition, os.environ['DNSE_API_KEY'], os.environ['DNSE_API_SECRET'],
                             duration, mode=mode, limits=limits, checkpoint=checkpoint, status_hook=status,
                             stop_file=ROOT/'data/control/STOP_DNSE_REALTIME')
    with exclusive_job(ROOT/'data/control/dnse_realtime.lock'):
        result = asyncio.run(execute())
    result.update(seed_checkpoint=seed, restored_checkpoint=restored)
    write_json(ROOT/'report/github/dnse_realtime_result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['smoke', 'collect'], default='smoke')
    parser.add_argument('--session', choices=['auto', 'morning', 'afternoon'], default='auto')
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    try:
        result = run(args.mode, args.session, args.seconds)
        print(json.dumps(result), flush=True)
        return 1 if result['state'].startswith('FAILED') else 0
    except Exception as error:
        code = str(error)
        if not re.fullmatch('[A-Z0-9_]{1,90}', code):
            code = type(error).__name__
        result = {'purpose': 'REALTIME_G1_COLLECTOR', 'state': 'FAILED', 'error_code': code}
        write_json(ROOT/'report/github/dnse_realtime_result.json', result)
        print(json.dumps(result), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
