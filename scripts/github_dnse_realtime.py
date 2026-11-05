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
import time

from config.settings import ROOT
from scripts.collect_dnse_realtime import session_plan
from scripts.github_checkpoint import ReleaseStore, backup_sqlite, state_key
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_realtime import collect, Limits, validate_catalog
from src.data_pipeline.dnse_realtime_store import audit
from src.models.orderbook import LOCAL_TZ
from scripts.normalize_dnse_realtime import normalize
from src.data_pipeline.dnse_ws_units import load_ws_rules
from scripts.github_dnse_backfill import run_backfill, closed_session

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


def recover_session(day, session, key, *, max_requests=6000):
    """Afternoon WS closes at 15:06; the existing REST day gate is 15:10."""
    rest_session = 'morning' if session == 'morning' else 'day'
    if rest_session == 'day':
        until = datetime.fromisoformat(day+'T15:10:00').replace(tzinfo=LOCAL_TZ)
        while datetime.now(LOCAL_TZ) < until:
            if (ROOT/'data/control/STOP_DNSE_REALTIME').exists() or (ROOT/'data/control/STOP_DNSE_HISTORY').exists():
                return dict(state='FAILED_REST_BACKFILL',error_code='REST_STOP_REQUESTED')
            time.sleep(min(1,max(0,(until-datetime.now(LOCAL_TZ)).total_seconds())))
    closed_session(day,rest_session)
    return run_backfill(day,rest_session,key,root=ROOT,max_requests=max_requests)


def recovery_result(day, session, recovered):
    return dict(provider='dnse',purpose='REALTIME_G1_COLLECTOR',date_local=day,session=session,
                state=('RECOVERED_REST_HISTORY_REALTIME_MISSED' if recovered['state']=='REST_BACKFILL_COMPLETED'
                       else 'FAILED_REST_BACKFILL'),realtime_window_missed=True,
                realtime_recovered=False,backfill=recovered)


def run(mode='smoke', session='auto', seconds=60, *, backfill_session='day', rest_max_requests=6000):
    require_environment()
    now = datetime.now(LOCAL_TZ)
    if os.getenv('GITHUB_EVENT_NAME') == 'schedule':
        scheduled = {'52 1 * * 1-5':'morning','52 5 * * 1-5':'afternoon'}
        if os.getenv('DNSE_SCHEDULE_CRON') not in scheduled:
            raise ValueError('WS_UNKNOWN_SCHEDULE')
        session = scheduled[os.environ['DNSE_SCHEDULE_CRON']]
    plan = session_plan(now, session)
    if mode in ('collect','backfill') and os.getenv('DNSE_REALTIME_ENABLED') != 'true':
        raise ValueError('WS_SCHEDULE_NOT_ENABLED')
    if not isinstance(rest_max_requests,int) or not 1 <= rest_max_requests <= 6000:
        raise ValueError('REST_REQUEST_LIMIT_INVALID')
    day = now.date().isoformat()
    if mode == 'backfill':
        return run_backfill(day,backfill_session,state_key(os.environ['LIQUIDITYLAB_STATE_KEY']),
                            root=ROOT,max_requests=rest_max_requests)
    if mode == 'collect' and not plan['ready']:
        if now.weekday() < 5 and now >= plan['end']:
            recovered = recover_session(day,plan['session'],state_key(os.environ['LIQUIDITYLAB_STATE_KEY']),
                                        max_requests=rest_max_requests)
            return recovery_result(day,plan['session'],recovered)
        return dict(state='FAILED_OUTSIDE_COLLECTION_WINDOW',purpose='REALTIME_G1_COLLECTOR',
                    date_local=day,session=plan['session'],error_code='WS_TOO_EARLY_OR_NON_WEEKDAY')
    if mode == 'smoke' and not 1 <= seconds <= 120:
        raise ValueError('WS_SMOKE_DURATION_INVALID')
    key = state_key(os.environ['LIQUIDITYLAB_STATE_KEY'])
    catalog, seed = load_seed(key)
    partition = 'smoke' if mode == 'smoke' else plan['session']
    day_key = day+'-'+partition
    folder = ROOT/'data/realtime/dnse'/day/partition
    folder.mkdir(parents=True, exist_ok=True)
    database = folder/'quotes.db'
    reports = ROOT/'report/realtime/dnse'/day/partition
    normalized_folder = ROOT/'data/processed/dnse_ws'/day/partition
    normalized_folder.mkdir(parents=True,exist_ok=True)
    normalized_database = normalized_folder/'normalized.db'
    normalized_store = ReleaseStore(REPOSITORY,key,namespace='dnse-ws-normalized')
    normalized_restored = None
    if not normalized_database.exists():
        normalized_restored = normalized_store.restore(day_key,normalized_database,reports/'normalized-restored')
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
            # Derived-data errors must not interrupt or discard successful raw persistence.
            try:
                with exclusive_job(ROOT/'data/control/dnse_ws_normalization.lock'):
                    converted = normalize(frozen/'frozen.db',normalized_database,rules=load_ws_rules())
                    derived = frozen/'normalized'
                    derived.mkdir()
                    write_json(derived/'audit.json',converted['audit'])
                    write_json(derived/'status.json',converted)
                    write_json(derived/'universe.json',catalog)
                    normalized_tag = normalized_store.publish(day_key,normalized_database,derived,
                        os.getenv('GITHUB_RUN_ID','manual'),os.getenv('GITHUB_RUN_ATTEMPT','1'))
                normalization = dict(state='SAVED_ENCRYPTED',normalization_version=converted['normalization_version'],
                    normalized_events=converted['audit']['normalized_events'],
                    quarantined_events=converted['audit']['quarantined_events'],
                    receipt_count=converted['audit']['receipt_count'],units_verified=False,
                    canonical_events=converted['audit']['canonical_events'],
                    canonical_financial_eligible=converted['audit']['canonical_financial_eligible'],
                    financial_eligible=False,checkpoint=normalized_tag,raw_checkpoint=tag,
                    restored_checkpoint=normalized_restored)
            except Exception as error:
                code=str(error)
                normalization=dict(state='FAILED_RESUMABLE',error_code=code if re.fullmatch('[A-Z0-9_]{1,90}',code) else type(error).__name__)
            write_json(ROOT/'report/github/dnse_ws_normalization_checkpoint.json',normalization)
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
    normalized_report=ROOT/'report/github/dnse_ws_normalization_checkpoint.json'
    if normalized_report.exists():
        result['normalization']=json.loads(normalized_report.read_text(encoding='utf-8'))
        if result['normalization']['state']=='FAILED_RESUMABLE' and not result['state'].startswith('FAILED'):
            result['state']='FAILED_NORMALIZATION_RESUMABLE'
    # A runner arriving during the session can observe the remainder, but cannot
    # recover its missed opening via WS. Preserve WS results and fetch REST separately.
    if mode == 'collect':
        result['realtime_window_missed'] = now > plan['start']
        if result['realtime_window_missed'] and result['state']=='WINDOW_ENDED_OBSERVED_ONLY':
            recovered = recover_session(day,plan['session'],key,max_requests=rest_max_requests)
            original = dict(result)
            result = {**result,**recovery_result(day,plan['session'],recovered),'realtime_result':original}
    write_json(ROOT/'report/github/dnse_realtime_result.json', result)
    return result


def exit_code(result):
    # Positive allowlist: unknown/skipped/paused/stopped states must not look successful.
    return 0 if result.get('state') in ('SMOKE_FINISHED','WINDOW_ENDED_OBSERVED_ONLY',
        'REST_BACKFILL_COMPLETED','RECOVERED_REST_HISTORY_REALTIME_MISSED') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['smoke', 'collect','backfill'], default='smoke')
    parser.add_argument('--session', choices=['auto', 'morning', 'afternoon'], default='auto')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--backfill-session',choices=['morning','day'],default='day')
    parser.add_argument('--rest-max-requests',type=int,default=6000)
    args = parser.parse_args()
    try:
        result = run(args.mode, args.session, args.seconds,backfill_session=args.backfill_session,
                     rest_max_requests=args.rest_max_requests)
        write_json(ROOT/'report/github/dnse_realtime_result.json', result)
        print(json.dumps(result), flush=True)
        return exit_code(result)
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
