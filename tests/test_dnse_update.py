from datetime import datetime
import json
import sqlite3

import pytest

from test_dnse_normalization import prepare_fixture, quote
from scripts import update_dnse_day as update


def test_plan_and_time_gate_do_not_start_collection_or_create_files(tmp_path,monkeypatch):
    now=datetime.fromisoformat('2026-10-29T14:45:00+07:00')
    assert not update.plan('2026-10-29',now=now)['ready']
    assert update.plan('2026-10-29','morning',now)['ready']
    with pytest.raises(ValueError,match='CLOSED_SESSION'):
        update.run('2026-10-29',root=tmp_path,now=now)
    assert list(tmp_path.iterdir())==[]


def test_one_command_runs_real_offline_pipeline_and_keeps_empty_distinct(tmp_path,quote,monkeypatch):
    path=prepare_fixture(tmp_path,quote)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO metadata VALUES('universe_complete','true')")
        rows=[dict(symbol=s,exchange=e,market_id=m) for s,e,m in db.execute('SELECT symbol,exchange,market_id FROM symbols')]
    (path.parent/'universe.json').write_text(json.dumps(dict(rows=rows)),encoding='utf-8')
    def forbidden(*args,**kwargs):
        raise AssertionError('Completed archives must not trigger new network collection')
    monkeypatch.setattr(update.collect_dnse_history,'run',forbidden)
    result=update.run('2026-10-28',root=tmp_path,export=True)
    assert result['state']=='COMPLETED'
    assert result['completed_steps']==update.STEPS
    assert result['collection_state']=='ALREADY_COMPLETE_NO_NETWORK'
    assert (tmp_path/'data/processed/dnse/quotes.normalized.jsonl.gz').exists()
    manifest=json.loads((tmp_path/'data/processed/dnse/manifest.json').read_text(encoding='utf-8'))
    assert manifest['files']['normalized_jsonl_gz']['records']==2
    assert manifest['unique_normalized_snapshots']==2
    report=json.loads((tmp_path/'report/dnse_missing_2026-10-28.json').read_text(encoding='utf-8'))
    assert report['empty_count']==1
    again=update.run('2026-10-28',root=tmp_path,export=False)
    assert again['state']=='COMPLETED'
    manifest=json.loads((tmp_path/'data/processed/dnse/manifest.json').read_text(encoding='utf-8'))
    assert not manifest['exports_refreshed'] and 'normalized_jsonl_gz' not in manifest['files']


def test_failed_collection_does_not_normalize_or_audit_partial_data(tmp_path,monkeypatch):
    monkeypatch.setattr(update.normalize_dnse_history,'prepare_rules',lambda root: None)
    monkeypatch.setattr(update,'load_rules',lambda path: None)
    monkeypatch.setattr(update.collect_dnse_history,'run',lambda *a,**kw: dict(state='PAUSED_REQUEST_LIMIT'))
    def forbidden(*args,**kwargs):
        raise AssertionError('Must not use an incomplete collection')
    monkeypatch.setattr(update.audit_dnse_history,'audit',forbidden)
    monkeypatch.setattr(update.normalize_dnse_history,'run',forbidden)
    result=update.run('2026-10-28',root=tmp_path)
    assert result['state']=='FAILED_RESUMABLE'
    assert result['completed_steps']==['preflight']
    assert result['error_code']=='COLLECTION_NOT_COMPLETE'
