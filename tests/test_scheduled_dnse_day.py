from datetime import datetime
import json

import pytest
from scripts import scheduled_dnse_day as scheduled


@pytest.fixture
def local_root(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/dnse_local_schedule.json').write_text(json.dumps(dict(enabled=True,
        start_date='2026-11-05',lookback_days=7,publish_web=True,excluded_dates=[])))
    monkeypatch.setattr(scheduled.update_dnse_day,'complete_archive',lambda *args:True)
    return tmp_path


def clock(value):
    return datetime.fromisoformat(value+'+07:00')


def test_1515_gate_and_morning_logon_catches_previous_day(local_root):
    assert scheduled.plan(local_root,clock('2026-11-05T15:14:59'))['due_dates']==[]
    assert scheduled.plan(local_root,clock('2026-11-05T15:15:00'))['due_dates']==['2026-11-05']
    assert scheduled.plan(local_root,clock('2026-11-06T08:00:00'))['due_dates']==['2026-11-05']
    assert scheduled.plan(local_root,clock('2026-11-09T16:00:00'))['due_dates']==['2026-11-05','2026-11-06','2026-11-09']


def test_completed_archive_with_public_day_is_not_repeated(local_root):
    (local_root/'report').mkdir()
    (local_root/'report/dnse_update_2026-11-05_day.json').write_text(json.dumps(dict(
        state='COMPLETED',completed_steps=scheduled.update_dnse_day.STEPS)))
    assert scheduled.plan(local_root,clock('2026-11-06T08:00:00'))['due_dates']==['2026-11-05']
    (local_root/'web/data').mkdir(parents=True)
    (local_root/'web/data/index.json').write_text(json.dumps(dict(days=[dict(date='2026-11-05')])))
    assert scheduled.plan(local_root,clock('2026-11-06T08:00:00'))['already_completed']==['2026-11-05']


def test_failed_day_is_resumed_and_failure_is_not_success(local_root,monkeypatch):
    calls=[]
    def fail(day,**kwargs):
        calls.append((day,kwargs));return dict(state='FAILED_RESUMABLE',error_code='DNSE_TIMEOUT')
    monkeypatch.setattr(scheduled.update_dnse_day,'run',fail)
    result=scheduled.run(local_root,clock('2026-11-06T16:00:00'))
    assert result['state']=='FAILED_RESUMABLE' and len(calls)==1
    assert calls[0][0]=='2026-11-05' and calls[0][1]['publish_web'] is True and calls[0][1]['export'] is False
    assert (local_root/'report/automation/dnse_local_schedule_result.json').exists()


def test_stop_flag_prevents_api_and_horizon_is_bounded(local_root,monkeypatch):
    (local_root/'data/control').mkdir(parents=True)
    (local_root/'data/control/STOP_DNSE_HISTORY').touch()
    monkeypatch.setattr(scheduled.update_dnse_day,'run',lambda *a,**k:pytest.fail('Stopped'))
    result=scheduled.run(local_root,clock('2026-11-20T16:00:00'))
    assert result['state']=='FAILED_RESUMABLE' and result['error_code']=='REST_STOP_REQUESTED'
    assert result['due_dates']==['2026-11-16','2026-11-17','2026-11-18','2026-11-19','2026-11-20']


def test_disabled_policy_and_explicit_exchange_holiday(local_root):
    p=local_root/'config/dnse_local_schedule.json';value=json.loads(p.read_text())
    value['excluded_dates']=['2026-11-05'];p.write_text(json.dumps(value))
    assert scheduled.plan(local_root,clock('2026-11-05T16:00:00'))['due_dates']==[]
    value['enabled']=False;p.write_text(json.dumps(value))
    assert scheduled.plan(local_root,clock('2026-11-06T16:00:00'))['state']=='DISABLED'
    with pytest.raises(ValueError,match='TIMEZONE'):
        scheduled.plan(local_root,datetime(2026,11,5,16))
