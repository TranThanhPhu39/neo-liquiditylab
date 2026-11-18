"""Independent Windows clock -> bounded GitHub workflow dispatch, no trading."""
from datetime import datetime, timedelta
import json
import os
import re

from config.settings import ROOT
from scripts.collect_dnse_realtime import session_plan
from scripts.github_api import GitHubAPI, repository_path
from scripts.github_ssi_realtime import LOCAL, REPOSITORY, policy
from scripts.ssi_schedule_guard import guard as ssi_guard
from src.data_pipeline.job_lock import exclusive_job
from scripts.scheduled_collection import write_json

ACTIVE = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}


def ensure_catalog(root, now):
    path = root / 'report/dnse_ws_catalog_preparation.json'
    previous = json.loads(path.read_bytes()) if path.exists() else {}
    if (previous.get('state') == 'CATALOG_PREPARED_AND_ENCRYPTED'
            and previous.get('as_of_date') == now.date().isoformat() and previous.get('encrypted_release')):
        return dict(state='CATALOG_ALREADY_CURRENT', as_of_date=previous['as_of_date'])
    if root.resolve() != ROOT.resolve():
        raise ValueError('CLOUD_GUARD_CATALOG_ROOT_INVALID')
    from scripts.prepare_dnse_realtime import prepare
    previous_flag = os.environ.get('DNSE_REALTIME_STORAGE_APPROVED')
    try:
        # The caller has checked the live repository approval flag first.
        os.environ['DNSE_REALTIME_STORAGE_APPROVED'] = 'true'
        return prepare(publish=True)
    finally:
        if previous_flag is None:os.environ.pop('DNSE_REALTIME_STORAGE_APPROVED', None)
        else:os.environ['DNSE_REALTIME_STORAGE_APPROVED'] = previous_flag


def dnse_guard(now, api, root=ROOT, refresh=ensure_catalog):
    if now.tzinfo is None:raise ValueError('CLOUD_GUARD_AWARE_CLOCK_REQUIRED')
    now = now.astimezone(LOCAL)
    p = policy(root)
    if now.weekday() >= 5 or now.date().isoformat() in p.get('excluded_dates', []):
        return dict(state='NON_TRADING_DATE')
    window = session_plan(now)
    if not window['ready']:return dict(state='OUTSIDE_COLLECTION_WINDOW')
    base = repository_path(REPOSITORY)
    for name in ('DNSE_REALTIME_ENABLED', 'DNSE_REALTIME_STORAGE_APPROVED'):
        if api.request('GET', base+'/actions/variables/'+name)['value'] != 'true':
            return dict(state='COLLECTION_DISABLED')
    runs = api.request('GET', base+'/actions/workflows/dnse-realtime.yml/runs?per_page=50')['workflow_runs']
    active = [r for r in runs if r['status'] in ACTIVE]
    if active:return dict(state='COLLECTOR_ALREADY_ACTIVE', run_ids=[r['id'] for r in active])
    start = window['start']-timedelta(minutes=10)
    attempts = [r for r in runs if start <= datetime.fromisoformat(r['created_at']).astimezone(LOCAL) <= now
                and not any(mode in r.get('display_title', '').lower() for mode in ('smoke', 'backfill'))]
    if any(r.get('conclusion') == 'success' for r in attempts):
        return dict(state='SESSION_ALREADY_COMPLETED', session=window['session'])
    if len(attempts) >= 2:return dict(state='RETRY_LIMIT_REACHED', session=window['session'])
    if attempts and now-max(datetime.fromisoformat(r.get('updated_at', r['created_at'])).astimezone(LOCAL)
                            for r in attempts) < timedelta(minutes=10):
        return dict(state='RETRY_COOLDOWN', session=window['session'])
    catalog = refresh(root, now)
    api.request('POST', base+'/actions/workflows/dnse-realtime.yml/dispatches', dict(ref='main', inputs=dict(
        mode='collect', session=window['session'], backfill_session='day', rest_max_requests='6000')))
    return dict(state='MISSED_SESSION_START_DISPATCHED', session=window['session'], day=now.date().isoformat(),
                late=now > window['start'], realtime_backfill_available=False,
                catalog_state=catalog['state'])


def error_code(error):
    code = str(error)
    return code if re.fullmatch(r'[A-Z0-9_]{1,90}', code) else type(error).__name__


def run(root=ROOT, now=None, api=None, refresh=ensure_catalog):
    now = now or datetime.now(LOCAL)
    if now.tzinfo is None:raise ValueError('CLOUD_GUARD_AWARE_CLOCK_REQUIRED')
    now = now.astimezone(LOCAL)
    result = dict(state='CHECKED', checked_at_local=now.isoformat(), independent_clock='WINDOWS_TASK_SCHEDULER',
                  requires_computer_on_and_user_logged_in=True, providers={})
    if (root/'data/control/STOP_GITHUB_DISPATCH').exists():
        return dict(result, state='STOPPED')
    p = policy(root)
    if now.weekday() >= 5 or now.date().isoformat() in p.get('excluded_dates', []):
        return dict(result, state='NON_TRADING_DATE')
    if not 8 <= now.hour <= 15:return dict(result, state='OUTSIDE_COLLECTION_WINDOW')
    with exclusive_job(root/'data/control/cloud-dispatch.lock'):
        client = api or GitHubAPI()
        for provider, check in [('ssi', lambda: ssi_guard(now, client, root=root)),
                                ('dnse', lambda: dnse_guard(now, client, root, refresh))]:
            try:result['providers'][provider] = check()
            except Exception as error:
                result['providers'][provider] = dict(state='FAILED_RESUMABLE', error_code=error_code(error))
        if any(r['state'] == 'FAILED_RESUMABLE' for r in result['providers'].values()):
            result['state'] = 'FAILED_RESUMABLE'
        write_json(root/'report/automation/cloud_dispatch_status.json', result)
    return result


if __name__ == '__main__':
    try:result = run()
    except Exception as error:result = dict(state='FAILED_RESUMABLE', error_code=error_code(error))
    print(json.dumps(result), flush=True)
    raise SystemExit(1 if result['state'] == 'FAILED_RESUMABLE' else 0)
