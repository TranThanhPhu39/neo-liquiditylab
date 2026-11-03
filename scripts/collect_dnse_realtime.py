"""Local entry for unscaled, durable DNSE G1 WebSocket collection."""
import argparse
import asyncio
from datetime import datetime, time
import hashlib
import json
import os
from pathlib import Path

from config.settings import ROOT
from scripts.collect_dnse_history import keep_awake
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.dnse_realtime import collect, validate_catalog
from src.models.orderbook import LOCAL_TZ


def session_plan(now, session='auto'):
    if now.tzinfo is None:
        raise ValueError('WS_TIMEZONE_REQUIRED')
    now = now.astimezone(LOCAL_TZ)
    if session == 'auto':
        session = 'morning' if now.time() < time(12) else 'afternoon'
    if session not in ('morning', 'afternoon'):
        raise ValueError('WS_SESSION_INVALID')
    start, end = (time(8,57), time(11,31)) if session == 'morning' else (time(12,57), time(15,6))
    start, end = (datetime.combine(now.date(), t, LOCAL_TZ) for t in (start, end))
    ready = now.weekday() < 5 and now < end and (start-now).total_seconds() <= 600
    return {'session': session, 'ready': ready, 'start': start, 'end': end,
            'start_delay_seconds': max(0, (start-now).total_seconds())}


def run_local(args):
    now = datetime.now(LOCAL_TZ)
    plan = session_plan(now, args.session)
    if args.mode == 'collect' and not plan['ready']:
        return {'state': 'SKIPPED_OUTSIDE_COLLECTION_WINDOW', 'date_local': now.date().isoformat()}
    catalog = json.loads(Path(args.universe).read_text(encoding='utf-8-sig'))
    validate_catalog(catalog, now.date())
    if args.symbols:
        wanted = set(args.symbols)
        if len(wanted) != len(args.symbols) or not wanted <= {r['symbol'] for r in catalog['rows']}:
            raise ValueError('WS_SYMBOLS_NOT_IN_CATALOG')
        catalog = {**catalog, 'rows': [r for r in catalog['rows'] if r['symbol'] in wanted], 'count': len(wanted)}
    scope = hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()[:8]
    partition = plan['session'] if args.mode == 'collect' else 'smoke-'+scope
    folder = ROOT/'data/realtime/dnse'/now.date().isoformat()/partition
    reports = ROOT/'report/realtime/dnse'/now.date().isoformat()/partition
    folder.mkdir(parents=True, exist_ok=True)
    def status(value):
        write_json(reports/'status.json', value)
        print(json.dumps(value), flush=True)
    async def execute():
        if args.mode == 'collect':
            while datetime.now(LOCAL_TZ) < plan['start']:
                if (ROOT/'data/control/STOP_DNSE_REALTIME').exists():
                    break
                await asyncio.sleep(min(1, max(0, (plan['start']-datetime.now(LOCAL_TZ)).total_seconds())))
        seconds = args.seconds if args.mode == 'smoke' else max(1, int((plan['end']-datetime.now(LOCAL_TZ)).total_seconds()))
        return await collect(folder/'quotes.db', catalog, now.date().isoformat(), partition,
                             os.getenv('DNSE_API_KEY'), os.getenv('DNSE_API_SECRET'), seconds,
                             mode=args.mode, status_hook=status, stop_file=ROOT/'data/control/STOP_DNSE_REALTIME')
    with exclusive_job(ROOT/'data/control/dnse_realtime.lock'), keep_awake():
        return asyncio.run(execute())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['smoke', 'collect'], default='smoke')
    parser.add_argument('--session', choices=['auto', 'morning', 'afternoon'], default='auto')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--universe', default=str(ROOT/'data/control/dnse_ws_catalog.json'))
    args = parser.parse_args()
    try:
        if args.mode == 'smoke' and not 1 <= args.seconds <= 300:
            raise ValueError('WS_SMOKE_DURATION_INVALID')
        result = run_local(args)
        print(json.dumps(result), flush=True)
        return 1 if result['state'].startswith('FAILED') else 0
    except Exception as error:
        code = str(error)
        if not __import__('re').fullmatch('[A-Z0-9_]{1,90}', code):
            code = type(error).__name__
        print(json.dumps({'state': 'FAILED', 'error_code': code}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
