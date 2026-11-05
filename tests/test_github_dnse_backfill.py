"""Offline failure/recovery tests with real REST archive, audit and resume logic."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from scripts import github_dnse_backfill as backfill, github_dnse_realtime as runner
from scripts.github_checkpoint import backup_sqlite
from scripts.sync_dnse_rest_history import restore
from src.data_providers.base import ProviderError
from src.models.orderbook import LOCAL_TZ

DAY='2026-11-04'


class Client:
    def __init__(self,fail=False):
        self.calls=[]
        self.fail=fail

    def get(self,path,params):
        self.calls.append((path,params))
        if self.fail:
            raise ProviderError('DNSE_TIMEOUT')
        if path=='/instruments':
            payload={'total':3,'data':[dict(symbol=s,marketId=m,securityGroupId='ST')
                                     for s,m in [('ACB','STO'),('CEO','STX'),('EMG','UPX')]]}
        else:
            symbol=path.split('/')[2]
            market={'ACB':'STO','CEO':'STX','EMG':'UPX'}[symbol]
            payload={'quotes':[] if symbol=='EMG' else [dict(symbol=symbol,marketId=market,
                boardId='G1',time=DAY+' 11:29:00.001',bid=[],offer=[])]}
        return dict(payload=payload,received_at_utc='2026-11-05T07:00:00+00:00')


class Store:
    """Capture SQLite backups at the encrypted ReleaseStore boundary; never call GitHub."""
    def __init__(self,folder):
        self.folder=folder
        folder.mkdir()
        self.latest={}
        self.published=[]

    def publish(self,partition,database,reports,run_id,attempt):
        tag='fixture-'+uuid.uuid4().hex
        path=self.folder/(tag+'.db')
        backup_sqlite(database,path)
        self.latest[partition]=(tag,path)
        self.published.append(json.loads((reports/'status.json').read_text(encoding='utf-8')))
        return tag

    def restore(self,partition,database,reports):
        if partition not in self.latest:
            return None
        tag,path=self.latest[partition]
        backup_sqlite(path,database)
        return tag


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'releases')


def test_partial_checkpoint_resumes_on_fresh_runner_and_empty_stays_distinct(tmp_path,store):
    first=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'one',max_requests=1,client=Client(),store=store)
    assert first['state']=='FAILED_REST_BACKFILL' and first['collection_state']=='PAUSED_REQUEST_LIMIT'
    assert first['checkpoint'] and first['incomplete_symbols_count']==2
    client=Client()
    second=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'two',client=client,store=store)
    assert second['state']=='REST_BACKFILL_COMPLETED'
    assert second['restored_checkpoint']==first['checkpoint']
    assert second['symbols_total']==second['symbols_fully_paginated']==3
    assert second['raw_quote_count']==2 and second['no_data_symbols_count']==1
    assert second['audit']['all_symbols_fully_paginated']
    assert len(client.calls)==2 and not any(path=='/instruments' for path,_ in client.calls)
    assert not second['realtime_recovered'] and not second['financial_eligible']
    with closing(sqlite3.connect(backfill.history_folder(tmp_path/'two',DAY,'morning')/'history.db')) as db:
        assert db.execute("SELECT status FROM symbols WHERE symbol='EMG'").fetchone()[0]=='NO_DATA_RETURNED'
    restored=restore(DAY,'morning',root=tmp_path/'local',store=store)
    assert restored['state']=='RESTORED'
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        restore(DAY,'morning',root=tmp_path/'local',store=store)


def test_network_failure_is_failed_durable_and_safe(tmp_path,store,monkeypatch):
    monkeypatch.setattr(backfill.collect_dnse_history.time,'sleep',lambda _:None)
    result=backfill.run_backfill(DAY,'day',b'x'*32,root=tmp_path/'run',client=Client(True),store=store)
    assert result['state']=='FAILED_REST_BACKFILL'
    assert result['error_code']=='DNSE_TIMEOUT' and result['checkpoint']
    assert result['symbols_total']==0 and 'audit' not in result
    assert runner.exit_code(result)==1


def test_checkpoint_failure_cannot_report_success(tmp_path,store,monkeypatch):
    def fail(*args):
        raise RuntimeError('synthetic private message')
    monkeypatch.setattr(store,'publish',fail)
    result=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'run',client=Client(),store=store)
    assert result['state']=='FAILED_REST_BACKFILL'
    assert result['checkpoint_error']=='RuntimeError'
    assert 'synthetic private' not in json.dumps(result)


def test_corrupt_page_fails_audit_and_preserves_checkpoint(tmp_path,store,monkeypatch):
    original=backfill.audit_dnse_history.audit
    def corrupt(day,**kwargs):
        with sqlite3.connect(backfill.history_folder(kwargs['root'],day,kwargs['session'])/'history.db') as db:
            db.execute("UPDATE pages SET sha256='corrupt' WHERE id=1")
        return original(day,**kwargs)
    monkeypatch.setattr(backfill.audit_dnse_history,'audit',corrupt)
    result=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'run',client=Client(),store=store)
    assert result['state']=='FAILED_REST_BACKFILL' and result['error_code']=='PAGE_CHECKSUM_FAILED'
    assert result['checkpoint']


@pytest.mark.parametrize('session,hour,minute,ready',[
    ('morning',11,29,False),('morning',11,30,True),('day',15,6,False),('day',15,10,True)])
def test_closed_session_gate(session,hour,minute,ready):
    now=datetime(2026,11,4,hour,minute,tzinfo=LOCAL_TZ)
    if ready:
        assert backfill.closed_session(DAY,session,now)<=now
    else:
        with pytest.raises(ValueError,match='NOT_CLOSED'):
            backfill.closed_session(DAY,session,now)


def configure(monkeypatch,tmp_path,hour,minute,weekday=4):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,11,weekday,hour,minute,tzinfo=LOCAL_TZ)
    monkeypatch.setattr(runner,'datetime',Clock)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(runner,'state_key',lambda _:b'x'*32)
    for name,value in dict(GITHUB_REPOSITORY=runner.REPOSITORY,GITHUB_REF='refs/heads/main',
        DNSE_API_KEY='fixture-key',DNSE_API_SECRET='fixture-secret',LIQUIDITYLAB_STATE_KEY='fixture',
        DNSE_REALTIME_STORAGE_APPROVED='true',DNSE_REALTIME_ENABLED='true',GITHUB_EVENT_NAME='schedule',
        DNSE_SCHEDULE_CRON='52 1 * * 1-5').items():
        monkeypatch.setenv(name,value)


@pytest.mark.parametrize('outcome,exit_status', [('REST_BACKFILL_COMPLETED',0),('FAILED_REST_BACKFILL',1)])
def test_late_morning_recovers_morning_even_when_delivered_in_afternoon(tmp_path,monkeypatch,outcome,exit_status):
    configure(monkeypatch,tmp_path,16,0)
    captured=[]
    def recover(day,session,key,**kwargs):
        captured.append((day,session))
        return dict(state=outcome,source='REST_HISTORY',checkpoint='fixture')
    monkeypatch.setattr(runner,'recover_session',recover)
    monkeypatch.setattr(runner,'load_seed',lambda _:pytest.fail('Late recovery must not need WS seed'))
    monkeypatch.setattr('sys.argv',['runner','--mode','collect'])
    assert runner.main()==exit_status
    saved=json.loads((tmp_path/'report/github/dnse_realtime_result.json').read_text())
    assert saved['realtime_window_missed'] and not saved['realtime_recovered']
    assert captured==[(DAY,'morning')]


def test_too_early_and_weekend_fail_without_network(tmp_path,monkeypatch):
    for day,hour in [(4,7),(7,16)]:
        configure(monkeypatch,tmp_path,hour,0,day)
        monkeypatch.setattr(runner,'recover_session',lambda *a,**kw:pytest.fail('No recovery for early/weekend'))
        result=runner.run('collect')
        assert result['state']=='FAILED_OUTSIDE_COLLECTION_WINDOW' and runner.exit_code(result)==1


def test_disabled_collection_never_falls_back(tmp_path,monkeypatch):
    configure(monkeypatch,tmp_path,16,0)
    monkeypatch.setenv('DNSE_REALTIME_ENABLED','false')
    monkeypatch.setattr(runner,'recover_session',lambda *a,**kw:pytest.fail('Disabled'))
    with pytest.raises(ValueError,match='NOT_ENABLED'):
        runner.run('collect')


def test_arrival_during_session_keeps_ws_then_recovers_missed_prefix(tmp_path,store,monkeypatch):
    configure(monkeypatch,tmp_path,10,0)
    monkeypatch.setattr(runner,'ReleaseStore',lambda *a,**kw:store)
    monkeypatch.setattr(runner,'load_seed',lambda _:({'count':3,'as_of_date':DAY},'fixture-catalog'))
    async def observed(*args,**kwargs):
        return dict(state='WINDOW_ENDED_OBSERVED_ONLY',purpose='REALTIME_G1_COLLECTOR',receipt_count=2)
    monkeypatch.setattr(runner,'collect',observed)
    monkeypatch.setattr(runner,'recover_session',lambda *a,**kw:dict(state='REST_BACKFILL_COMPLETED'))
    result=runner.run('collect')
    assert result['state']=='RECOVERED_REST_HISTORY_REALTIME_MISSED'
    assert result['realtime_result']['receipt_count']==2 and result['realtime_window_missed']
    assert not result['realtime_recovered']


@pytest.mark.parametrize('state',['SKIPPED_OUTSIDE_COLLECTION_WINDOW','PAUSED_TIME_LIMIT',
    'STOPPED_BY_USER','FAILED_REST_BACKFILL','UNEXPECTED_FUTURE_STATE'])
def test_non_success_states_are_red(state):
    assert runner.exit_code(dict(state=state))==1


def test_workflow_preserves_crons_and_always_reports_recovery():
    import yaml
    source=(Path(__file__).resolve().parents[1]/'.github/workflows/dnse-realtime.yml').read_text(encoding='utf-8')
    workflow=yaml.load(source,Loader=yaml.BaseLoader)
    assert workflow['on']['schedule']==[{'cron':'52 1 * * 1-5'},{'cron':'52 5 * * 1-5'}]
    steps=workflow['jobs']['collector']['steps']
    assert steps[-1]['if']=='always()' and 'realtime_window_missed' in steps[-1]['run']
    # An explicitly invalid numeric zero must reach validation, not become a full-budget run.
    collector=next(s for s in steps if s.get('name')=='Restore collect normalize audit and save encrypted checkpoints')
    assert collector['env']['REST_MAX_REQUESTS']=='${{ inputs.rest_max_requests }}'
    assert '${REST_MAX_REQUESTS:-6000}' in collector['run']
    assert 'upload-artifact' not in source


def test_exact_request_budget_completion_is_not_marked_partial(tmp_path,store):
    result=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'run',
                                max_requests=3,client=Client(),store=store)
    assert result['state']=='REST_BACKFILL_COMPLETED'


def test_elapsed_budget_preserves_partial_archive(tmp_path,store,monkeypatch):
    ticks=iter([0,0,0,5,6,7])
    monkeypatch.setattr(backfill.time,'monotonic',lambda:next(ticks,10))
    result=backfill.run_backfill(DAY,'morning',b'x'*32,root=tmp_path/'run',
                                max_seconds=1,client=Client(),store=store)
    assert result['state']=='FAILED_REST_BACKFILL' and result['collection_state']=='PAUSED_TIME_LIMIT'
    assert result['checkpoint'] and result['incomplete_symbols_count']==3


def test_afternoon_recovery_waits_until_rest_day_is_closed(tmp_path,monkeypatch):
    current=[datetime(2026,11,4,15,6,tzinfo=LOCAL_TZ)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return current[0]
    monkeypatch.setattr(runner,'datetime',Clock)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    def advance(seconds):
        assert seconds<=1
        current[0]=datetime(2026,11,4,15,10,tzinfo=LOCAL_TZ)
    monkeypatch.setattr(runner.time,'sleep',advance)
    def capture(day,session,key,**kwargs):
        assert current[0].minute==10 and session=='day'
        return dict(state='REST_BACKFILL_COMPLETED')
    monkeypatch.setattr(runner,'run_backfill',capture)
    assert runner.recover_session(DAY,'afternoon',b'x'*32)['state']=='REST_BACKFILL_COMPLETED'


def test_stop_file_prevents_new_rest_requests(tmp_path,store):
    root=tmp_path/'run'
    (root/'data/control').mkdir(parents=True)
    (root/'data/control/STOP_DNSE_HISTORY').touch()
    client=Client()
    result=backfill.run_backfill(DAY,'morning',b'x'*32,root=root,client=client,store=store)
    assert result['state']=='FAILED_REST_BACKFILL' and result['error_code']=='REST_STOP_REQUESTED'
    assert client.calls==[] and store.published==[]


def test_monitor_accepts_recovery_counters_but_never_quote_or_auth_payloads():
    from scripts.github_monitor import summarize_log
    good=runner.recovery_result(DAY,'morning',dict(purpose='REST_HISTORY_BACKFILL',
        state='FAILED_REST_BACKFILL',error_code='DNSE_TIMEOUT',checkpoint='fixture'))
    assert summarize_log(json.dumps(good))['worker_summaries']==[good]
    good['backfill']['secret']='fixture-private'
    assert summarize_log(json.dumps(good))['worker_summaries']==[]
