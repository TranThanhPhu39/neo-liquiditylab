"""Reconcile DNSE and SSI delivery for one Vietnam trading date."""
import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode

from config.settings import ROOT
from scripts.github_api import GitHubAPI, GitHubError, repository_path
from scripts.github_ssi_realtime import LOCAL, REPOSITORY, outcome, plan, policy
from scripts.scheduled_collection import exclusive_job, write_json
from src.data_pipeline.parquet_history import ParquetHistory, checked_folder, digest

BASE = repository_path(REPOSITORY)
SYMBOLS = ('ACB', 'FPT', 'HPG')


def read(path):
    return json.loads(path.read_bytes()) if path.exists() else {}


def stage(action):
    try:
        return action()
    except (HTTPError, GitHubError) as error:
        return dict(state='CHECK_FAILED', error_code=f'HTTP_{error.code if isinstance(error,HTTPError) else error.status}')
    except Exception as error:
        code = str(error)
        return dict(state='CHECK_FAILED', error_code=code if re.fullmatch('[A-Z0-9_]{1,100}', code) else type(error).__name__)


def wait_for_host(request, seconds):
    deadline = monotonic()+seconds
    while True:
        hosting = stage(lambda:request('/api/hosting'))
        if (hosting.get('seed_ready') and hosting.get('recovery_state')=='READY') or monotonic()>=deadline:
            return hosting
        sleep(min(8,max(0,deadline-monotonic())))


def index_by_symbol(entries):
    groups = {}
    for entry in entries:
        if entry['symbol'] in groups:
            raise ValueError('DUPLICATE_ACTIVE_WEB_SYMBOL')
        groups[entry['symbol']] = entry
    return groups


def verify_dnse_web(day, expected, request):
    hosting = request('/api/hosting')
    if not hosting.get('seed_ready'):
        return dict(state='WAITING_FOR_WEB_RESTORE')
    actual = index_by_symbol(e for e in request('/api/datasets')['datasets'] if e['day'] == day and not e.get('archived'))
    if not actual:
        return dict(state='DAY_NOT_ON_WEB')
    if actual.keys() != expected.keys():
        return dict(state='WEB_CATALOG_MISMATCH', missing=len(expected.keys()-actual.keys()),
                    extra=len(actual.keys()-expected.keys()))
    for symbol, item in expected.items():
        shown = actual[symbol]
        if (shown.get('observations') != item['observations'] or
                (not item['observations'] and shown.get('availability') != item['availability'])):
            return dict(state='WEB_OBSERVATIONS_MISMATCH', symbol=symbol)
    # Exercise both the independently reviewed replay and a market projection.
    verified = []
    market = next((e['symbol'] for e in actual.values() if e.get('market_observation') and e['observations']), None)
    for symbol in dict.fromkeys((*SYMBOLS, market)):
        if not symbol or symbol not in actual or not actual[symbol]['observations']:
            continue
        item = actual[symbol]
        replay = request('/api/dataset?' + urlencode(dict(dataset_id=item['dataset_id'])))
        if (replay.get('dataset_id') != item['dataset_id'] or
                len(replay.get('frames', [])) != item['observations']):
            return dict(state='WEB_REPLAY_MISMATCH', symbol=symbol)
        verified.append(symbol)
    if not verified:
        return dict(state='NO_USABLE_WEB_REPLAY')
    return dict(state='VERIFIED', symbols=len(actual), snapshots=sum(e['observations'] for e in actual.values()),
                replay_symbols=verified, checked_at_utc=datetime.now(timezone.utc).isoformat())


def dnse(day, root, api, request):
    raw = read(root/'report'/f'dnse_history_audit_{day}.json')
    update = read(root/'report'/f'dnse_update_{day}_day.json')
    rows = raw.get('raw_quote_count_verified')
    observed = raw.get('symbols_with_records')
    total = raw.get('symbols_total')
    paginated = raw.get('all_symbols_fully_paginated') and raw.get('sqlite_integrity') == 'ok'
    result = dict(collection=dict(state=('PAGINATED_WITH_OBSERVATIONS' if rows else 'SOURCE_EMPTY') if paginated
                                 else 'COLLECTION_NOT_VERIFIED', raw_rows=rows, symbols=total,
                                 symbols_with_records=observed, no_data_returned=raw.get('symbol_status',{}).get('NO_DATA_RETURNED'),
                                 error_code=update.get('error_code')),
                  normalization=dict(state='NOT_VERIFIED'), checkpoint=dict(state='NOT_VERIFIED'),
                  web=dict(state='NOT_VERIFIED'), market_coverage='PROVIDER_COMPLETENESS_UNVERIFIED')
    if not raw and datetime.now(LOCAL)<datetime.combine(date.fromisoformat(day),time(15,15),LOCAL):
        result['collection']['state']='WAITING_FOR_DAILY_WINDOW'
        return dict(result, processing_state='WAITING_FOR_DAILY_WINDOW')
    if not paginated or not rows:
        return dict(result, processing_state='NO_USABLE_SOURCE' if paginated else 'COLLECTION_NOT_VERIFIED')
    if observed < total:
        result['market_coverage'] = 'SYMBOLS_WITHOUT_SOURCE_OBSERVATIONS'
    history = ParquetHistory(root/'data/compact/catalog.json')
    entry = history.days.get(day)
    if not entry or entry.get('state') != 'SOURCE_AND_PARQUET_ALL_ROWS_VERIFIED':
        return dict(result, processing_state='NORMALIZATION_NOT_VERIFIED')
    folder, manifest = checked_folder(root, entry)
    archives = history.metadata(day)['archives']
    if (not any(a['path']==f'data/history/dnse/{day}/history.db' and a['raw_records']==rows for a in archives) or
            sum(a['raw_records'] for a in archives) != entry['lineage_rows'] or
            entry['snapshots'] != manifest['snapshots']):
        raise ValueError('DNSE_SOURCE_PARQUET_RECONCILIATION_FAILED')
    result['normalization'] = dict(state='VERIFIED', snapshots=entry['snapshots'], lineage_rows=entry['lineage_rows'])
    upstream = digest(folder/'manifest.json')
    projection = read(root/'data/control/market-projection-cache'/f'{day}-{upstream[:16]}'/'manifest.json')
    if projection.get('upstream_manifest_sha256') != upstream or projection.get('rows') != entry['snapshots']:
        return dict(result, processing_state='PROJECTION_NOT_VERIFIED')
    expected = index_by_symbol(projection['datasets'])
    if set(expected)!=set(history.catalog(day)) or sum(e['observations'] for e in expected.values())!=entry['snapshots']:
        raise ValueError('DNSE_PROJECTION_RECONCILIATION_FAILED')
    # The preparation report records a download, decrypt and checksum pass of
    # exactly this projection. Confirm its release still exists without fetching
    # the entire historical bootstrap again on every evening scheduler tick.
    signature = hashlib.sha256(json.dumps(projection['files'], sort_keys=True).encode()).hexdigest()[:16]
    tag = f'monitor-web-seed-market-{day}-{signature}'
    proof = read(root/'report'/f'online_monitor_preparation_market_{day}_{signature}.json')
    if (proof.get('tag') == tag and proof.get('remote_recovery_verified') and
            proof.get('remote_proof',{}).get('state') == 'DECRYPTED_AND_VERIFIED' and api is not None):
        def verify_release():
            release = api.request('GET', BASE+'/releases/tags/'+tag)
            if (release.get('draft') or release.get('tag_name')!=tag or
                    {a['name'] for a in release['assets']}!=set(proof['assets']) or
                    sum(a['size'] for a in release['assets'])!=proof['encrypted_bytes']):
                return dict(state='RELEASE_ASSET_MISMATCH', tag=tag)
            return dict(state='VERIFIED', tag=tag, evidence='RECORDED_REMOTE_RESTORE_AND_CURRENT_ASSETS')
        result['checkpoint'] = stage(verify_release)
    result['web'] = stage(lambda: verify_dnse_web(day, expected, request))
    complete = all(result[k]['state']=='VERIFIED' for k in ('normalization','checkpoint','web'))
    return dict(result, processing_state='VERIFIED' if complete else 'DELIVERY_PENDING_OR_FAILED')


def verify_ssi_records(files, manifest, proof):
    import pyarrow as pa
    import pyarrow.parquet as pq
    quotes, trades, units = Counter(), Counter(), Counter()
    count = 0
    for item in manifest['files']:
        part = pq.ParquetFile(pa.BufferReader(files['run/'+item['path']]))
        for batch in part.iter_batches(columns=['record_json'], batch_size=512):
            for value in batch.column(0).to_pylist():
                record = json.loads(value)
                if record['ordinal'] != count:
                    raise ValueError('SSI_RECORD_ORDINAL_MISMATCH')
                count += 1
                kind, symbol = record['kind'], record['symbol']
                if kind == 'QUOTE':
                    frame = record['frame']
                    if record['quote_index'] != quotes[symbol] or frame['index'] != quotes[symbol]:
                        raise ValueError('SSI_NORMALIZED_QUOTE_INDEX_MISMATCH')
                    if not isinstance(frame['bids'],list) or not isinstance(frame['asks'],list):
                        raise ValueError('SSI_NORMALIZED_BOOK_INVALID')
                    quotes[symbol] += 1
                    units[frame['units']] += 1
                elif kind == 'TRADE':
                    trade = record['trade']
                    if not trade['source_time'] or trade['qty'] <= 0:
                        raise ValueError('SSI_NORMALIZED_TRADE_INVALID')
                    trades[symbol] += 1
    expected = set(manifest['symbols'])
    if (expected != set(SYMBOLS) or not (set(quotes)|set(trades)).issubset(expected) or count != proof['records'] or
            {s:quotes[s] for s in expected} != proof['quote_counts'] or
            {s:trades[s] for s in expected} != proof['trade_counts']):
        raise ValueError('SSI_RECORD_COUNTS_MISMATCH')
    return dict(state='VERIFIED' if quotes or trades else 'NO_MARKET_OBSERVATIONS', unit_counts=dict(units))


def verify_ssi_web(day, proof, web_runs, request):
    if isinstance(web_runs,dict):
        return web_runs
    run = next((r for r in web_runs if r['day']==day and r['run_id']==proof['run_id']), None)
    if not run:
        return dict(state='CAPTURE_NOT_ON_WEB')
    if any(run[name] != proof[name] for name in ('quote_counts','trade_counts')):
        return dict(state='WEB_CAPTURE_COUNTS_MISMATCH')
    verified = []
    for symbol, total in proof['quote_counts'].items():
        if not total:
            continue
        params = urlencode(dict(run_id=proof['run_id'], symbol=symbol, start=max(0, total-500)))
        view = request('/api/live/view?'+params)
        if (view.get('run_id')!=proof['run_id'] or view.get('day')!=day or view.get('symbol')!=symbol or
                view.get('total')!=total or view.get('trade_total')!=proof['trade_counts'][symbol] or
                not view.get('frames') or view['frames'][-1]['index']!=total-1):
            return dict(state='WEB_CAPTURE_REPLAY_MISMATCH', symbol=symbol)
        verified.append(symbol)
    return dict(state='VERIFIED' if verified else 'NO_USABLE_WEB_REPLAY', replay_symbols=verified)


def ssi_capture(day, session, release, restore, web_runs, request, schedule):
    tag = release['tag_name']
    files, proof = restore(tag)
    manifest = json.loads(files['run/manifest.json'])
    status = json.loads(files['status.json'])
    if manifest['day'] != day or manifest['run_id'] != proof['run_id']:
        raise ValueError('SSI_CHECKPOINT_DAY_OR_ID_MISMATCH')
    final = release['name'].startswith('Encrypted SSI session ') and not status.get('active')
    window = plan(datetime.combine(date.fromisoformat(day),time(0),LOCAL),session,schedule)
    coverage = outcome(status,window) if final else 'PARTIAL_CHECKPOINT_ONLY'
    if coverage == 'COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED':
        closed_at = manifest.get('closed_at')
        close = datetime.fromisoformat(window['close'])
        ended_on_time = closed_at and datetime.fromisoformat(closed_at) >= close-timedelta(seconds=5)
        coverage = ('NO_KNOWN_GAPS_PROVIDER_COMPLETENESS_UNVERIFIED'
                    if ended_on_time and status.get('reason')=='DURATION_LIMIT' else 'PARTIAL_EARLY_OR_UNVERIFIED_END')
    normalization = stage(lambda: verify_ssi_records(files, manifest, proof))
    web = stage(lambda: verify_ssi_web(day, proof, web_runs, request))
    return dict(tag=tag, origin='WINDOWS_LOCAL_FALLBACK' if f'-{session}-local-' in tag else 'GITHUB',
                run_id=proof['run_id'], final=final,
                processing_state='VERIFIED' if normalization['state']==web['state']=='VERIFIED' and final else 'PENDING_OR_NO_USABLE_REPLAY',
                normalization=normalization, checkpoint=dict(state=proof['state'], records=proof['records']), web=web,
                market_coverage=coverage, provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE',
                first_connected_at=status.get('first_connected_at'), last_received=status.get('last_received'),
                gaps=status.get('gaps'), rejected=status.get('rejected'),
                quotes=proof['quote_counts'], trades=proof['trade_counts'])


def ssi_attempts(day, api, schedule):
    from scripts.ssi_schedule_guard import run_session

    start = datetime.combine(date.fromisoformat(day), time(0), LOCAL)
    end = start + timedelta(days=1)
    query = urlencode(dict(created=f'{start.astimezone(timezone.utc).isoformat()}..{end.astimezone(timezone.utc).isoformat()}',
                           branch='main', per_page=100))
    runs = api.request('GET',BASE+'/actions/workflows/ssi-realtime.yml/runs?'+query)['workflow_runs']
    grouped = {s:[] for s in schedule['sessions']}
    for run in runs:
        session = run_session(run,schedule)
        created = datetime.fromisoformat(run['created_at']).astimezone(LOCAL)
        if session not in grouped or not start <= created < end:
            continue
        close = datetime.fromisoformat(plan(start,session,schedule)['close'])
        grouped[session].append(dict(run_id=run['id'], created_at_local=created.isoformat(), status=run['status'],
                                     conclusion=run.get('conclusion'), created_after_close=created>=close))
    return grouped


def ssi_session(day, session, api, restore, web_runs, request, schedule, attempts):
    if api is None:
        return dict(state='GITHUB_UNAVAILABLE', captures=[])
    prefix = f'ssi-ws-{day}-{session}-'
    refs = api.request('GET', BASE+'/git/matching-refs/tags/'+prefix)
    tags = sorted((r['ref'].removeprefix('refs/tags/') for r in refs), reverse=True)
    captures = []
    # One capture per origin. Prefer the newest final release, otherwise
    # retain the newest temporary checkpoint as explicitly partial evidence.
    for local in (False,True):
        candidates = [t for t in tags if bool(t.startswith(prefix+'local-'))==local]
        selected = None
        for tag in candidates[:12]:
            try:
                release = api.request('GET', BASE+'/releases/tags/'+tag)
            except GitHubError as error:
                if error.status == 404:
                    continue
                raise
            if release.get('draft') or release.get('tag_name') != tag:
                continue
            if selected is None or release['name'].startswith('Encrypted SSI session '):
                selected = release
            if release['name'].startswith('Encrypted SSI session '):
                break
        if selected:
            capture = stage(lambda: ssi_capture(day,session,selected,restore,web_runs,request,schedule))
            captures.append(dict(capture,tag=selected['tag_name']))
    runs = attempts.get(session,[])
    state = 'CAPTURES_CHECKED' if captures else 'NO_VERIFIED_CAPTURE'
    if not captures:
        now = datetime.now(LOCAL)
        close = datetime.fromisoformat(plan(datetime.combine(date.fromisoformat(day),time(0),LOCAL),session,schedule)['close'])
        if now < close:
            state = 'SESSION_OPEN_OR_NOT_STARTED'
        elif runs and all(r['created_after_close'] for r in runs):
            state = 'NO_CAPTURE_GITHUB_RUN_CREATED_AFTER_CLOSE'
    return dict(state=state, captures=captures, github_runs=runs,
                github_run_check=attempts.get('state','VERIFIED'))


def local_ssi_status(day, session, root):
    saved = read(root/'report/automation/ssi-local'/f'{day}-{session}.json')
    if not saved:
        return dict(state='NO_LOCAL_REPORT')
    capture = saved.get('capture',{})
    final = saved.get('final_checkpoint') or {}
    return dict(state=saved['state'], processing_state=saved.get('processing_state'),
                collection_state=saved.get('collection_state'), run_id=saved.get('capture_run_id'),
                quotes=capture.get('quote_counts'), trades=capture.get('trade_counts'),
                checkpoint_tag=final.get('tag'), heartbeat_at_local=saved.get('heartbeat_at_local'))


def audit(day, *, root=ROOT, api=None, request=None, restore=None, wait_seconds=180):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('YYYY_MM_DD_REQUIRED')
    root=Path(root).resolve()
    if request is None:
        from scripts.verify_monitor_hosted import request
    if api is None:
        try:
            api = GitHubAPI()
        except (OSError, RuntimeError):
            pass
    if restore is None:
        def restore(tag):
            from scripts.restore_ssi_checkpoint import recovery_key
            from scripts.ssi_checkpoint import SSICheckpoints
            return SSICheckpoints(REPOSITORY,recovery_key(root),api=api).restore(tag,max_bytes=64*1024*1024)
    schedule=policy(root)
    with exclusive_job(root/'data/control/daily-delivery-audit.lock'):
        hosting = wait_for_host(request,wait_seconds)
        web_runs = stage(lambda: request('/api/live/runs'))
        attempts = stage(lambda: ssi_attempts(day,api,schedule)) if api else dict(state='GITHUB_UNAVAILABLE')
        sessions = {}
        for session in ('morning','afternoon'):
            sessions[session] = stage(lambda: ssi_session(day,session,api,restore,web_runs,request,schedule,attempts))
            sessions[session]['local_task'] = stage(lambda:local_ssi_status(day,session,root))
        result = dict(date=day, checked_at_utc=datetime.now(timezone.utc).isoformat(), hosting=hosting,
                      dnse=stage(lambda: dnse(day,root,api,request)), ssi=sessions)
        write_json(root/'report/automation/daily-delivery'/f'{day}.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--date')
    mode.add_argument('--scheduled',action='store_true',help='Check the latest closed trading date')
    args = parser.parse_args()
    now = datetime.now(LOCAL)
    selected = now.date()
    if args.scheduled:
        from scripts.scheduled_dnse_day import policy as dnse_policy
        config = dnse_policy(ROOT)
        if not config['enabled']:
            return
        if now.time() < time(15,15):
            selected -= timedelta(days=1)
        excluded = set(config.get('excluded_dates',[]))
        while selected.weekday()>=5 or selected.isoformat() in excluded:
            selected -= timedelta(days=1)
        if selected.isoformat() < config['start_date']:
            return
    print(json.dumps(audit(args.date or selected.isoformat()),ensure_ascii=True))


if __name__ == '__main__':
    main()
