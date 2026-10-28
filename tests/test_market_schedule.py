from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import scheduled_collection
from src.data_pipeline.market_schedule import build_universe,chunks,in_window,open_exchanges,quota_plan
from src.data_pipeline.storage import Storage
from src.data_pipeline.raw_migration import migrate_raw
from tests.test_raw_migration import contract_file, seed
from src.data_pipeline.unit_contracts import load_unit_contract


@pytest.fixture
def market_config():
    return json.loads(Path('config/market_collection.json').read_text(encoding='utf-8'))


def account(day=10000,month=100000):
    return {'authenticated':True,'limits':{'per_minute':60,'per_hour':3600,'per_day':day,'per_month':month},
            'used':{'minute':0,'hour':0,'day':0,'month':0}}


def test_universe_is_equities_in_three_exchanges_with_dedup():
    response={'rows':[{'symbol':'aaa','exchange':'HOSE'},{'symbol':'AAA','exchange':'HOSE'},
                      {'symbol':'BBB','exchange':'HNX'},{'symbol':'CCC','exchange':'UPCOM'},
                      {'symbol':'OLD','exchange':'DELISTED'}]}
    rows,excluded=build_universe(response,['HOSE','HNX','UPCOM'])
    assert len(rows)==3 and len(excluded)==1
    assert {r['symbol'] for r in rows}=={'AAA','BBB','CCC'}
    with pytest.raises(ValueError):
        build_universe({'rows':response['rows'][:2]},['HOSE','HNX','UPCOM'])
    with pytest.raises(ValueError):
        build_universe({'rows':[{'symbol':'AAA','exchange':'HOSE'},{'symbol':'AAA','exchange':'HNX'}]},['HOSE','HNX'])


def test_disjoint_batches_cover_every_symbol_once(market_config):
    batches=chunks(list(range(1520)),100)
    assert len(batches)==16 and len(batches[-1])==20
    assert [x for batch in batches for x in batch]==list(range(1520))
    plan=quota_plan(1520,account(),market_config)
    assert plan['cycle_seconds']==180 and plan['batch_spacing_seconds']==11.25
    assert plan['estimated_daily_sdk_calls'] < 7500
    restricted=quota_plan(1520,account(day=100,month=3000),market_config)
    assert restricted['cycle_seconds']>180
    with pytest.raises(ValueError):
        quota_plan(1520,account(day=1),market_config)


@pytest.mark.parametrize('at,expected',[
    ('2026-10-29T09:14:59+07:00',False),('2026-10-29T09:15:00+07:00',True),
    ('2026-10-29T11:30:00+07:00',False),('2026-10-29T13:00:00+07:00',True),
    ('2026-10-29T14:30:00+07:00',False),('2026-10-31T10:00:00+07:00',False)])
def test_collection_boundaries(at,expected,market_config):
    assert in_window(datetime.fromisoformat(at),market_config)==expected
    market_config['holidays']=['2026-10-29']
    assert not in_window(datetime.fromisoformat(at),market_config)


def test_status_only_opens_current_equities():
    now=datetime.fromisoformat('2026-10-29T10:00:00+07:00')
    stamp=now.timestamp()*1000
    response={'rows':[{'exchange':'HOSE','asset_type':'EQUITY','status':'OPEN','timestamp':stamp},
                      {'exchange':'HNX','asset_type':'DERIVATIVES','status':'OPEN','timestamp':stamp},
                      {'exchange':'UPCOM','asset_type':'EQUITY','status':'CLOSED','timestamp':stamp}]}
    assert open_exchanges(response,now)=={'HOSE'}
    response['rows'][0]['timestamp']-=121000
    with pytest.raises(ValueError):
        open_exchanges(response,now)


def test_bulk_normalization_does_not_extend_review_scope(tmp_path,book):
    with Storage(tmp_path/'scope.db') as db:
        seed(db,book)
        contract=load_unit_contract(contract_file(tmp_path))
        assert migrate_raw(db,contract,symbols={'FPT'})['inserted']==0
        assert not db.query() and len(db.raw_batches())==1
        assert migrate_raw(db,contract,symbols={'HPG'})['inserted']==1
        assert db.raw_batches(batch_ids=[])==[]


def test_daily_export_streams_and_compressed_backup_is_valid(tmp_path,book,monkeypatch):
    monkeypatch.setattr(scheduled_collection,'ROOT',tmp_path)
    folder=tmp_path/'report'
    folder.mkdir()
    with Storage(tmp_path/'data.db') as db:
        seed(db,book)
        report=scheduled_collection.daily_audit(db,book.received_at_local.date(),folder)
        assert report['symbol_observations']==1 and report['distinct_symbols']==1
        archive=Path(scheduled_collection.compressed_backup(db,tmp_path/'data/backups/automation'))
        restored=tmp_path/'restored.db'
        with gzip.open(archive,'rb') as source:
            restored.write_bytes(source.read())
        with sqlite3.connect(restored) as restored_db:
            assert restored_db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert restored_db.execute('SELECT COUNT(*) FROM raw_quote_batches').fetchone()[0]==1
        assert len(list(archive.parent.glob('*.db')))==0


def test_stop_switch_and_neo_handoff_are_checked_without_token(tmp_path,market_config,monkeypatch):
    monkeypatch.setattr(scheduled_collection,'ROOT',tmp_path)
    (tmp_path/'config').mkdir()
    path=tmp_path/'config/market_collection.json'
    path.write_text(json.dumps(market_config))
    assert scheduled_collection.stop_reason(market_config) is None
    stop=tmp_path/market_config['neo_ready_file']
    stop.parent.mkdir(parents=True)
    stop.touch()
    assert scheduled_collection.stop_reason(market_config)=='NEO_HANDOFF_READY'
    market_config['enabled']=False
    path.write_text(json.dumps(market_config))
    assert scheduled_collection.stop_reason(market_config)=='DISABLED'


def test_process_lock_prevents_two_collectors(tmp_path):
    with scheduled_collection.exclusive_job(tmp_path/'collector.lock'):
        with pytest.raises(RuntimeError,match='ANOTHER'):
            with scheduled_collection.exclusive_job(tmp_path/'collector.lock'):
                pass


def test_daily_job_collects_all_but_normalizes_only_reviewed_symbols(tmp_path,book,market_config,monkeypatch):
    monkeypatch.setattr(scheduled_collection,'ROOT',tmp_path)
    config=market_config
    config['collection_windows']=[['09:15','09:16']]
    config['minimum_free_disk_gb']=0
    config['unit_contract']=str(contract_file(tmp_path).relative_to(tmp_path))
    config['normalize_symbols']=['FPT']
    (tmp_path/'config').mkdir()
    (tmp_path/'config/market_collection.json').write_text(json.dumps(config))
    current=[datetime.fromisoformat('2026-10-29T09:15:00+07:00')]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return current[0].astimezone(tz or timezone.utc)
    monkeypatch.setattr(scheduled_collection,'datetime',Clock)
    def wait(target,config):
        current[0]=max(current[0],target)
        return True
    monkeypatch.setattr(scheduled_collection,'wait_until',wait)
    universe=[{'symbol':'FPT','exchange':'HOSE'},{'symbol':'AAA','exchange':'HNX'},{'symbol':'BBB','exchange':'UPCOM'}]
    calls=[]
    def request(operation):
        calls.append(operation)
        if operation=='universe':
            return {'rows':universe,'account':account()}
        return {'rows':[{'exchange':e,'asset_type':'EQUITY','status':'OPEN','timestamp':current[0].timestamp()*1000} for e in ('HOSE','HNX','UPCOM')]}
    monkeypatch.setattr(scheduled_collection,'sdk_request',request)
    def capture(db,symbols,**kwargs):
        assert set(symbols)=={'FPT','AAA','BBB'}
        run=db.start_run('vnstock',symbols)
        response={'version':'5.2.1','rows':[dict(book.to_record(),symbol=row['symbol'],exchange=row['exchange']) for row in universe],
                  'missing_columns':[],'received_at_utc':current[0].astimezone(timezone.utc).isoformat()}
        db.insert_raw_batch(response,run)
        db.finish_run(run,'RAW_ONLY',3)
        return {'run_id':run,'status':'RAW_ONLY','inserted_rows':3,'missing_symbols':[], 'received_at_utc':response['received_at_utc']}
    monkeypatch.setattr(scheduled_collection,'collect_raw_once',capture)
    assert scheduled_collection.run_day(config)==0
    status=json.loads((tmp_path/'report/automation/2026-10-29/status.json').read_text())
    assert status['state']=='COMPLETED' and status['audit']['symbol_observations']==3
    assert status['audit']['normalized_snapshots']==1
    assert Path(status['backup_path']).is_file()
    # A repeated logon must not start another collection job for the same completed day.
    current[0]=datetime.fromisoformat('2026-10-29T09:15:30+07:00')
    assert scheduled_collection.run_day(config)==0
    assert calls==['universe','market_status']


def test_failure_is_recorded_without_automatic_same_day_retry(tmp_path,market_config,monkeypatch):
    monkeypatch.setattr(scheduled_collection,'ROOT',tmp_path)
    config=market_config
    config['minimum_free_disk_gb']=0
    (tmp_path/'config').mkdir()
    (tmp_path/'config/market_collection.json').write_text(json.dumps(config))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.fromisoformat('2026-10-29T09:15:00+07:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(scheduled_collection,'datetime',Clock)
    calls=[]
    def fail(operation):
        calls.append(operation)
        raise RuntimeError('AUTHENTICATION_REQUIRED')
    monkeypatch.setattr(scheduled_collection,'sdk_request',fail)
    assert scheduled_collection.run_day(config)==1
    assert scheduled_collection.run_day(config)==0
    status=json.loads((tmp_path/'report/automation/2026-10-29/status.json').read_text())
    assert status['state']=='FAILED' and status['error_code']=='AUTHENTICATION_REQUIRED'
    assert status['audit']['symbol_observations']==0 and calls==['universe']


def test_early_boot_skips_without_authentication_or_waiting(tmp_path,market_config,monkeypatch):
    monkeypatch.setattr(scheduled_collection,'ROOT',tmp_path)
    (tmp_path/'config').mkdir()
    (tmp_path/'config/market_collection.json').write_text(json.dumps(market_config))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.fromisoformat('2026-10-29T06:00:00+07:00').astimezone(tz or timezone.utc)
    monkeypatch.setattr(scheduled_collection,'datetime',Clock)
    def forbidden(*args,**kwargs):
        raise AssertionError('No SDK calls before scheduled start')
    monkeypatch.setattr(scheduled_collection,'sdk_request',forbidden)
    assert scheduled_collection.run_day(market_config)==0
    report=json.loads((tmp_path/'report/automation/latest.json').read_text())
    assert report['state']=='SKIPPED_BEFORE_START'
