"""Recheck an exhausted, all-empty DNSE day without overwriting its evidence."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from config.settings import ROOT
from scripts import collect_dnse_history as collector, audit_dnse_history
from scripts.scheduled_collection import exclusive_job, write_json


def raw_folder(root, day, session):
    # Validate before using user input in paths.
    datetime.strptime(day, '%Y-%m-%d')
    if session not in ('day', 'morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    folder = Path(root) / 'data/history/dnse' / day
    return folder if session == 'day' else folder / session


def archive_info(folder):
    with closing(sqlite3.connect((folder / 'history.db').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        symbols = list(db.execute('SELECT symbol,exchange,market_id FROM symbols ORDER BY symbol'))
        terminal = not db.execute("SELECT 1 FROM symbols WHERE status NOT IN ('COMPLETE','NO_DATA_RETURNED') LIMIT 1").fetchone()
        count = db.execute('SELECT coalesce(sum(quote_count),0) FROM pages').fetchone()[0]
    return dict(settings=json.loads(metadata['settings']), symbols=symbols,
                empty=bool(metadata.get('universe_complete') and symbols and terminal and count == 0),
                quotes=count)


def recheck(day, *, root=ROOT, session='day', full_scan=False, client=None, now=None,
            max_requests=6000, max_seconds=5400):
    root = Path(root).resolve()
    source = raw_folder(root, day, session)
    info = archive_info(source)
    if not info['empty']:
        raise ValueError('RECHECK_REQUIRES_TERMINAL_EMPTY_ARCHIVE')
    if info['settings']['date'] != day or info['settings'].get('session', 'day') != session:
        raise ValueError('RAW_ARCHIVE_SETTINGS_MISMATCH')
    now = now or datetime.now(timezone.utc)
    target = root / 'report' / f'dnse_empty_recheck_{day}_{session}.json'
    previous = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    if previous.get('state') == 'SOURCE_EMPTY_AFTER_RECHECK' and not full_scan:
        elapsed = (now - datetime.fromisoformat(previous['checked_at_utc'])).total_seconds()
        if 0 <= elapsed < 1800:
            return previous
    if previous.get('state') in ('RUNNING', 'PAUSED_RESUMABLE', 'RECOVERED_STAGED'):
        attempt = (root / previous['attempt_root']).resolve()
        attempt.relative_to(root / 'data/control/dnse-retry')
        result = previous
        if previous['state'] == 'RECOVERED_STAGED':
            return previous
    else:
        # The nested collector adds its own data/history/day suffix. Keep the
        # attempt prefix short for SQLite's Windows filename limit.
        attempt = root / 'data/control/dnse-retry' / uuid4().hex
        folder = raw_folder(attempt, day, session)
        folder.mkdir(parents=True)
        with closing(collector.connect(folder / 'history.db')) as db:
            collector.initialize(db, day, info['settings']['page_size'], session)
            with db:
                db.executemany('INSERT INTO symbols(symbol,exchange,market_id) VALUES(?,?,?)', info['symbols'])
                db.execute("INSERT INTO metadata VALUES('universe_complete','true')")
        write_json(folder / 'universe.json', dict(rows=[dict(symbol=s, exchange=e, market_id=m)
                                                     for s, e, m in info['symbols']]))
        result = dict(date=day, session=session, state='RUNNING', checked_at_utc=now.isoformat(),
                      attempt_root=str(attempt.relative_to(root)), probes=[], full_scan=full_scan)
    client = client or collector.DNSEReadOnly(min_interval=0.5)
    def check_stop(_=None):
        if (root / 'data/control/STOP_DNSE_HISTORY').exists():
            raise ValueError('REST_STOP_REQUESTED')
    check_stop()
    write_json(target, result)
    folder = raw_folder(attempt, day, session)
    if not full_scan and not result.get('full_scan'):
        # A small fresh check on liquid symbols prevents re-downloading an unavailable
        # whole day on every scheduler retry. Calls are counted in the attempt ledger.
        with closing(collector.connect(folder / 'history.db')) as db:
            available = {row[0] for row in info['symbols']}
            selected = [s for s in ('ACB', 'FPT', 'HPG') if s in available] or sorted(available)[:3]
            for symbol in selected:
                check_stop()
                observation = collector.request_with_retries(db, client, f'/price/{symbol}/quotes',
                    dict(boardId='G1', order='ASC', limit=1,
                         **{key: info['settings'][key] for key in ('from', 'to')}), symbol)
                payload = observation.get('payload', {})
                quotes = payload.get('quotes')
                if not isinstance(quotes, list):
                    raise ValueError('DNSE_RECHECK_RESPONSE_INVALID')
                result['probes'].append(dict(symbol=symbol, records=len(quotes)))
        result['checked_at_utc'] = now.isoformat()
        if not any(item['records'] for item in result['probes']):
            result['state'] = 'SOURCE_EMPTY_AFTER_RECHECK'
            write_json(target, result)
            return result
    result.update(state='RUNNING', full_scan=True)
    write_json(target, result)
    collected = collector.run(day, root=attempt, session=session, client=client,
                              page_size=info['settings']['page_size'], max_requests=max_requests,
                              max_seconds=max_seconds, progress_hook=check_stop)
    result.update(collection_state=collected['state'], raw_quote_count=collected['raw_quote_count'],
                  checked_at_utc=collector.utcnow())
    if collected['state'] != 'COMPLETED':
        result['state'] = 'PAUSED_RESUMABLE'
    else:
        audited = audit_dnse_history.audit(day, root=attempt, session=session)
        if not audited['all_symbols_fully_paginated']:
            raise ValueError('RECHECK_AUDIT_INCOMPLETE')
        result['audit'] = dict(raw_quote_count_verified=audited['raw_quote_count_verified'],
                               symbols_total=audited['symbols_total'], symbols_with_records=audited['symbols_with_records'])
        result['state'] = 'RECOVERED_STAGED' if audited['raw_quote_count_verified'] else 'SOURCE_EMPTY_AFTER_RECHECK'
    write_json(target, result)
    return result


def promote(day, result, *, root=ROOT, session='day'):
    """Install only an audited nonempty retry; preserve old raw and disposable work."""
    root = Path(root).resolve()
    attempt = (root / result['attempt_root']).resolve()
    attempt.relative_to(root / 'data/control/dnse-retry')
    source = raw_folder(root, day, session)
    recovered = raw_folder(attempt, day, session)
    if result['state'] != 'RECOVERED_STAGED' or not archive_info(source)['empty']:
        raise ValueError('RECHECK_PROMOTION_PRECONDITION_FAILED')
    audit = audit_dnse_history.audit(day, root=attempt, session=session)
    if not audit['all_symbols_fully_paginated'] or not audit['raw_quote_count_verified']:
        raise ValueError('RECHECK_PROMOTION_AUDIT_FAILED')
    backup = attempt / 'previous-empty-archive'
    work = root / 'data/processed/dnse/work' / day
    # All paths are resolved and fenced before directory moves on Windows.
    for path in (source, recovered, backup, work):
        path.resolve().relative_to(root)
    with exclusive_job(root / 'data/control/dnse-compaction.lock'), exclusive_job(root / 'data/control/dnse_history.lock'):
        if not archive_info(source)['empty']:
            raise ValueError('RECHECK_PROMOTION_PRECONDITION_FAILED')
        catalog = root / 'data/compact/catalog.json'
        if catalog.exists():
            # Recheck under the publication lock: no canonical source is replaced.
            content = json.loads(catalog.read_text(encoding='utf-8'))
            if any(entry.get('day') == day for entry in content.get('quote_days', [])):
                raise ValueError('RECHECK_CANONICAL_DAY_ALREADY_EXISTS')
        # A full-day archive can contain an independently collected morning.
        # Preserve its discoverable path as well as the untouched backup copy.
        if session == 'day' and (source / 'morning').exists() and not (recovered / 'morning').exists():
            shutil.copytree(source / 'morning', recovered / 'morning')
        saved_work = attempt / 'previous-normalization-work'
        if work.exists():
            work.rename(saved_work)
        try:
            source.rename(backup)
            try:
                recovered.rename(source)
            except BaseException:
                backup.rename(source)
                raise
        except BaseException:
            if saved_work.exists():
                saved_work.rename(work)
            raise
    result.update(state='RECOVERED_INSTALLED', previous_archive=str(backup.relative_to(root)))
    write_json(root / 'report' / f'dnse_empty_recheck_{day}_{session}.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--session', choices=['day', 'morning'], default='day')
    parser.add_argument('--full-scan', action='store_true', help='Retry the entire saved universe in an isolated archive')
    args = parser.parse_args()
    with exclusive_job(ROOT / 'data/control/dnse_update.lock'):
        result = recheck(args.date, session=args.session, full_scan=args.full_scan)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['state'] == 'RECOVERED_STAGED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
