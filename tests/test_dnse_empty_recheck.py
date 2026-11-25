"""Empty upstream responses must remain missing and retry without losing evidence."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json

import pytest

from scripts import collect_dnse_history as collector, recheck_empty_dnse_day as retry
from scripts import update_dnse_day as update
from test_dnse_normalization import quote

DAY = '2026-10-28'
NOW = datetime(2026, 11, 25, 14, tzinfo=timezone.utc)


def empty_archive(root):
    folder = retry.raw_folder(root, DAY, 'day')
    folder.mkdir(parents=True)
    symbols = [('ACB', 'HOSE', 'STO'), ('FPT', 'HOSE', 'STO'), ('HPG', 'HOSE', 'STO')]
    with closing(collector.connect(folder / 'history.db')) as db:
        settings = collector.initialize(db, DAY, 1000)
        with db:
            db.executemany('INSERT INTO symbols(symbol,exchange,market_id) VALUES(?,?,?)', symbols)
            db.execute("INSERT INTO metadata VALUES('universe_complete','true')")
        for row in db.execute('SELECT * FROM symbols').fetchall():
            collector.store_page(db, row, dict(received_at_utc=NOW.isoformat(), payload={'quotes': []}), settings)
    collector.write_json(folder / 'universe.json', dict(rows=[dict(symbol=s, exchange=e, market_id=m) for s, e, m in symbols]))
    return folder


class API:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params))
        assert path.startswith('/price/') and path.endswith('/quotes')
        quotes = [self.record] if self.record and '/ACB/' in path else []
        return dict(received_at_utc=NOW.isoformat(), payload={'quotes': quotes})


def test_empty_recheck_is_bounded_cached_and_preserves_raw(tmp_path):
    folder = empty_archive(tmp_path)
    before = hashlib.sha256((folder / 'history.db').read_bytes()).hexdigest()
    api = API()
    result = retry.recheck(DAY, root=tmp_path, client=api, now=NOW)
    assert result['state'] == 'SOURCE_EMPTY_AFTER_RECHECK'
    assert len(api.calls) == 3
    assert retry.recheck(DAY, root=tmp_path, client=api, now=NOW + timedelta(minutes=29)) == result
    assert len(api.calls) == 3
    retry.recheck(DAY, root=tmp_path, client=api, now=NOW + timedelta(minutes=31))
    assert len(api.calls) == 6
    assert hashlib.sha256((folder / 'history.db').read_bytes()).hexdigest() == before
    assert not (tmp_path / 'data/compact/catalog.json').exists()


def test_positive_recheck_downloads_audits_and_preserves_empty_archive_on_install(tmp_path, quote):
    folder = empty_archive(tmp_path)
    before = (folder / 'history.db').read_bytes()
    work = tmp_path / 'data/processed/dnse/work' / DAY
    work.mkdir(parents=True)
    (work / 'normalized.db').write_bytes(b'old disposable work')
    api = API(quote)
    result = retry.recheck(DAY, root=tmp_path, client=api, now=NOW)
    assert result['state'] == 'RECOVERED_STAGED'
    assert result['audit'] == dict(raw_quote_count_verified=1, symbols_total=3, symbols_with_records=1)
    assert len(api.calls) == 6  # Three probes, then an independent complete pagination.
    assert (folder / 'history.db').read_bytes() == before
    installed = retry.promote(DAY, result, root=tmp_path)
    assert installed['state'] == 'RECOVERED_INSTALLED'
    assert (tmp_path / installed['previous_archive'] / 'history.db').read_bytes() == before
    assert retry.archive_info(folder)['quotes'] == 1
    attempt = tmp_path / installed['attempt_root']
    assert (attempt / 'previous-normalization-work/normalized.db').read_bytes() == b'old disposable work'
    assert not work.exists()


def test_full_retry_all_empty_does_not_install_or_invent_zero_books(tmp_path):
    folder = empty_archive(tmp_path)
    before = (folder / 'history.db').read_bytes()
    api = API()
    result = retry.recheck(DAY, root=tmp_path, client=api, now=NOW, full_scan=True)
    assert result['state'] == 'SOURCE_EMPTY_AFTER_RECHECK'
    assert result['audit']['raw_quote_count_verified'] == 0
    assert len(api.calls) == 3
    with pytest.raises(ValueError, match='PROMOTION_PRECONDITION'):
        retry.promote(DAY, result, root=tmp_path)
    assert (folder / 'history.db').read_bytes() == before


def test_retry_cannot_replace_sources_of_existing_canonical_day(tmp_path, quote):
    folder = empty_archive(tmp_path)
    before = (folder / 'history.db').read_bytes()
    result = retry.recheck(DAY, root=tmp_path, client=API(quote), now=NOW)
    catalog = tmp_path / 'data/compact/catalog.json'
    collector.write_json(catalog, dict(quote_days=[dict(day=DAY)]))
    with pytest.raises(ValueError, match='CANONICAL_DAY_ALREADY_EXISTS'):
        retry.promote(DAY, result, root=tmp_path)
    assert (folder / 'history.db').read_bytes() == before


def test_failed_or_paused_retry_resumes_same_attempt(tmp_path, quote, monkeypatch):
    empty_archive(tmp_path)
    real_run = collector.run
    monkeypatch.setattr(collector, 'run', lambda *a, **kw: dict(state='PAUSED_REQUEST_LIMIT', raw_quote_count=0))
    result = retry.recheck(DAY, root=tmp_path, client=API(quote), now=NOW, full_scan=True)
    assert result['state'] == 'PAUSED_RESUMABLE'
    monkeypatch.setattr(collector, 'run', real_run)
    resumed = retry.recheck(DAY, root=tmp_path, client=API(quote), now=NOW)
    assert resumed['state'] == 'RECOVERED_STAGED'
    assert resumed['attempt_root'] == result['attempt_root']


def test_update_refuses_normalization_when_cached_day_remains_empty(tmp_path, monkeypatch):
    empty_archive(tmp_path)
    monkeypatch.setattr(update.normalize_dnse_history, 'prepare_rules', lambda root: None)
    monkeypatch.setattr(update, 'load_rules', lambda path: None)
    monkeypatch.setattr(retry, 'recheck', lambda *a, **kw: dict(state='SOURCE_EMPTY_AFTER_RECHECK'))
    monkeypatch.setattr(update.normalize_dnse_history, 'run', lambda **kw: pytest.fail('Empty day must not normalize'))
    result = update.run(DAY, root=tmp_path, publish_web=False)
    assert result['state'] == 'FAILED_RESUMABLE'
    assert result['collection_state'] == 'SOURCE_EMPTY_AFTER_RECHECK'
    assert result['error_code'] == 'DNSE_DAY_SOURCE_EMPTY_AFTER_RECHECK'
    assert result['completed_steps'] == ['preflight']


def test_newly_exhausted_empty_day_fails_before_normalization(tmp_path, monkeypatch):
    empty_archive(tmp_path)
    monkeypatch.setattr(update.normalize_dnse_history, 'prepare_rules', lambda root: None)
    monkeypatch.setattr(update, 'load_rules', lambda path: None)
    monkeypatch.setattr(update, 'complete_archive', lambda *a: False)
    monkeypatch.setattr(collector, 'run', lambda *a, **kw: dict(state='COMPLETED'))
    monkeypatch.setattr(update.normalize_dnse_history, 'run', lambda **kw: pytest.fail('Empty day must not normalize'))
    result = update.run(DAY, root=tmp_path, publish_web=False)
    assert result['state'] == 'FAILED_RESUMABLE'
    assert result['error_code'] == 'DNSE_DAY_SOURCE_EMPTY_ALL_SYMBOLS'
    assert result['raw_quote_count'] == result['symbols_with_records'] == 0
