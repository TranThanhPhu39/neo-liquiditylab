"""Resume closed-session REST history in encrypted Releases, separate from WS."""
from contextlib import closing
from datetime import date, datetime, time as clock_time
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from config.settings import ROOT
from scripts import collect_dnse_history, audit_dnse_history
from scripts.github_checkpoint import ReleaseStore
from scripts.scheduled_collection import exclusive_job, write_json
from src.models.orderbook import LOCAL_TZ

REPOSITORY = 'TranThanhPhu39/neo-liquiditylab'
NAMESPACE = 'dnse-rest-history'


def history_folder(root, day, session):
    folder = Path(root)/'data/history/dnse'/day
    return folder/'morning' if session == 'morning' else folder


def closed_session(day, session, now=None):
    if session not in ('morning', 'day'):
        raise ValueError('REST_INVALID_SESSION')
    source_day = date.fromisoformat(day)
    now = now or datetime.now(LOCAL_TZ)
    if now.tzinfo is None:
        raise ValueError('REST_TIMEZONE_REQUIRED')
    end = datetime.combine(source_day, clock_time(11,30) if session == 'morning' else clock_time(15,10), LOCAL_TZ)
    if source_day.weekday() >= 5:
        raise ValueError('REST_NON_WEEKDAY_REQUIRES_MANUAL_REVIEW')
    if end > now:
        raise ValueError('REST_SESSION_NOT_CLOSED')
    return end


def safe_error(error):
    code = str(error)
    return code if re.fullmatch('[A-Z0-9_]{1,90}', code) else type(error).__name__


def restore_universe(database, folder):
    """The resumable collector skips instruments when its DB already has the catalog."""
    with closing(sqlite3.connect(database)) as db:
        if not db.execute("SELECT 1 FROM metadata WHERE key='universe_complete'").fetchone():
            return
        rows = [dict(symbol=s, exchange=e, market_id=m) for s,e,m in db.execute(
            'SELECT symbol,exchange,market_id FROM symbols ORDER BY symbol')]
    write_json(folder/'universe.json', dict(provider='dnse',count=len(rows),rows=rows))


def run_backfill(day, session, key, *, root=ROOT, max_requests=6000,
                 max_seconds=5400, client=None, store=None):
    closed_session(day, session)
    if not isinstance(max_requests, int) or not 1 <= max_requests <= 6000:
        raise ValueError('REST_REQUEST_LIMIT_INVALID')
    if not 1 <= max_seconds <= 5400:
        raise ValueError('REST_TIME_LIMIT_INVALID')
    root = Path(root)
    folder = history_folder(root, day, session)
    folder.mkdir(parents=True, exist_ok=True)
    database = folder/'history.db'
    reports = root/'report/github/rest-backfill'/day/session
    partition = day+'-'+session
    store = store or ReleaseStore(REPOSITORY, key, namespace=NAMESPACE)
    result = dict(provider='dnse',purpose='REST_HISTORY_BACKFILL',state='FAILED_REST_BACKFILL',
                  date_local=day,session=session,source='REST_HISTORY',units_verified=False,
                  financial_eligible=False,realtime_recovered=False,checkpoint=None,
                  restored_checkpoint=None)

    def save(value, audit=None):
        write_json(reports/'status.json', value)
        write_json(reports/'audit.json', audit or dict(state='PARTIAL_NOT_AUDITED'))
        if (folder/'universe.json').exists():
            write_json(reports/'universe.json', json.loads((folder/'universe.json').read_text(encoding='utf-8')))
        return store.publish(partition, database, reports,
                             os.getenv('GITHUB_RUN_ID','manual'), os.getenv('GITHUB_RUN_ATTEMPT','1'))

    last_checkpoint = time.monotonic()
    def progress(value):
        nonlocal last_checkpoint
        if time.monotonic()-last_checkpoint >= 300:
            result['checkpoint'] = save({**result,'collection':value})
            last_checkpoint = time.monotonic()

    with exclusive_job(root/'data/control/dnse_rest_backfill.lock'):
        try:
            if (root/'data/control/STOP_DNSE_HISTORY').exists():
                raise ValueError('REST_STOP_REQUESTED')
            if not database.exists():
                result['restored_checkpoint'] = store.restore(partition, database, reports/'restored'/uuid.uuid4().hex)
            if database.exists():
                restore_universe(database, folder)
            collected = collect_dnse_history.run(day,root=root,session=session,client=client,
                max_requests=max_requests,max_seconds=max_seconds,progress_hook=progress)
            result['collection_state'] = collected['state']
            for name in ('symbols_total','symbols_fully_paginated','pages_saved','raw_quote_count','sqlite_integrity'):
                result[name] = collected.get(name)
            result['no_data_symbols_count'] = len(collected.get('no_data_symbols',[]))
            result['incomplete_symbols_count'] = len(collected.get('incomplete_symbols',[]))
            if collected.get('error_code'):
                result['error_code'] = collected['error_code']
            if collected['state'] == 'COMPLETED' and collected['symbols_total'] > 0:
                checked = audit_dnse_history.audit(day,root=root,session=session)
                if checked['sqlite_integrity'] != 'ok' or not checked['all_symbols_fully_paginated']:
                    raise ValueError('REST_HISTORY_AUDIT_FAILED')
                result['state'] = 'REST_BACKFILL_COMPLETED'
                result['audit'] = {k:checked[k] for k in ('sqlite_integrity','all_symbols_fully_paginated',
                    'page_checksums_verified','raw_quote_count_verified','symbols_with_records')}
            else:
                result.setdefault('error_code','REST_HISTORY_NOT_COMPLETE')
        except Exception as error:
            result.update(state='FAILED_REST_BACKFILL',error_code=safe_error(error))
        # A failed/paused download is also durable and resumable. No plaintext artifact uploads.
        if database.exists():
            try:
                result['checkpoint'] = save(result, result.get('audit'))
            except Exception as error:
                result.update(state='FAILED_REST_BACKFILL',checkpoint_error=safe_error(error))
        else:
            result.update(state='FAILED_REST_BACKFILL',error_code=result.get('error_code','REST_DATABASE_NOT_CREATED'))
        write_json(reports/'result.json',result)
    return result
