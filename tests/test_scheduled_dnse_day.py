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
    p=scheduled.plan(local_root,clock('2026-11-06T08:00:00'))
    assert p['due_dates']==[] and p['pages_publication_pending']
    (local_root/'web/data').mkdir(parents=True)
    (local_root/'web/data/index.json').write_text(json.dumps(dict(days=[dict(date='2026-11-05')])))
    assert scheduled.plan(local_root,clock('2026-11-06T08:00:00'))['already_completed']==['2026-11-05']


def test_failed_day_is_resumed_and_failure_is_not_success(local_root,monkeypatch):
    calls=[]
    def fail(day,**kwargs):
        calls.append((day,kwargs));return dict(state='FAILED_RESUMABLE',error_code='DNSE_TIMEOUT')
    monkeypatch.setattr(scheduled.update_dnse_day,'run',fail)
    result=scheduled.run(local_root,clock('2026-11-06T16:00:00'))
    assert result['state']=='FAILED_RESUMABLE' and len(calls)==2
    assert calls[0][0]=='2026-11-05' and calls[0][1]['publish_web'] is False and calls[0][1]['export'] is False
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


def test_publication_failure_does_not_prevent_collecting_newer_due_day(local_root,monkeypatch):
    from scripts import publish_dnse_web,publish_daily_monitor
    calls=[]
    def collect(day,**kwargs):
        calls.append(day)
        path=local_root/'report'/f'dnse_update_{day}_day.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(state='COMPLETED',completed_steps=scheduled.update_dnse_day.STEPS)))
        return dict(state='COMPLETED')
    monkeypatch.setattr(scheduled.update_dnse_day,'run',collect)
    monkeypatch.setattr(publish_dnse_web,'publish_samples',lambda root:(_ for _ in ()).throw(ValueError('UPLOAD_RETRY')))
    monkeypatch.setattr(publish_daily_monitor,'run',lambda root:dict(state='COMPLETED'))
    result=scheduled.run(local_root,clock('2026-11-06T16:00:00'))
    assert calls==['2026-11-05','2026-11-06']
    assert result['state']=='FAILED_RESUMABLE' and result['pages_publication_pending']
    assert result['monitor_publication']['state']=='COMPLETED'
    next_plan=scheduled.plan(local_root,clock('2026-11-06T16:01:00'))
    assert next_plan['due_dates']==[] and next_plan['pages_publication_pending']


def test_failed_old_raw_day_does_not_block_new_day(local_root,monkeypatch):
    from scripts import publish_dnse_web,publish_daily_monitor
    calls=[]
    def collect(day,**kwargs):
        calls.append(day)
        return dict(state='FAILED_RESUMABLE' if day=='2026-11-05' else 'COMPLETED')
    monkeypatch.setattr(scheduled.update_dnse_day,'run',collect)
    monkeypatch.setattr(publish_dnse_web,'publish_samples',lambda root:dict(state='PUBLIC_SAMPLES_PUSHED'))
    monkeypatch.setattr(publish_daily_monitor,'run',lambda root:dict(state='COMPLETED'))
    result=scheduled.run(local_root,clock('2026-11-06T16:00:00'))
    assert calls==['2026-11-05','2026-11-06'] and result['state']=='FAILED_RESUMABLE'
    assert result['monitor_publication']['state']=='COMPLETED'


def test_failed_old_day_cannot_block_retry_of_already_normalized_day(local_root,monkeypatch):
    from scripts import publish_dnse_web,publish_daily_monitor
    report=local_root/'report'
    report.mkdir()
    (report/'dnse_update_2026-11-06_day.json').write_text(json.dumps(dict(
        state='COMPLETED',completed_steps=scheduled.update_dnse_day.STEPS)))
    monkeypatch.setattr(scheduled.update_dnse_day,'run',lambda *args,**kwargs:dict(
        state='FAILED_RESUMABLE',error_code='DNSE_DAY_SOURCE_EMPTY_AFTER_RECHECK'))
    monkeypatch.setattr(publish_dnse_web,'publish_samples',lambda root:dict(state='PUBLIC_SAMPLES_PUSHED'))
    monkeypatch.setattr(publish_daily_monitor,'run',lambda root:dict(state='COMPLETED',day='2026-11-06'))
    result=scheduled.run(local_root,clock('2026-11-06T16:00:00'))
    assert result['state']=='FAILED_RESUMABLE' and result['due_dates']==['2026-11-05']
    assert result['monitor_publication']['day']=='2026-11-06'
    assert not result['pages_publication_pending']
