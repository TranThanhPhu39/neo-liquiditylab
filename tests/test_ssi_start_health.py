from datetime import datetime, timedelta
import json
from pathlib import Path

from scripts.ssi_schedule_guard import guard
from scripts import scheduled_cloud_guard_entry as entry
from src.monitoring.ssi_health import ConnectionHealth
from test_ssi_guard_contract import API


def clock(value):return datetime.fromisoformat(value)


def queued(session='morning', **changes):
    return dict(dict(id=1,status='queued',display_title='SSI collect '+session,head_branch='main',
                     created_at='2026-11-26T08:15:00+07:00'),**changes)


def test_guard_prewarms_and_dates_dispatch_without_claiming_worker_started():
    api=API()
    result=guard(clock('2026-11-26T08:15:00+07:00'),api)
    assert result['state']=='MISSED_SESSION_START_DISPATCHED'
    assert api.posts[0]['inputs']['collection_date']=='2026-11-26'
    assert not result['worker_start_verified'] and not result['market_coverage_verified']
    assert guard(clock('2026-11-26T08:14:59+07:00'),API())['state']=='OUTSIDE_COLLECTION_WINDOW'


def test_old_unstarted_session_is_cancelled_without_touching_running_writer():
    api=API([queued()])
    result=guard(clock('2026-11-26T12:15:00+07:00'),api)
    assert result['cancelled_expired_queued_runs']==[1]
    assert result['session']=='afternoon' and result['state']=='MISSED_SESSION_START_DISPATCHED'
    assert api.posts[0] is None and api.posts[1]['inputs']['session']=='afternoon'
    api=API([queued(status='in_progress')])
    assert guard(clock('2026-11-26T12:15:00+07:00'),api)['state']=='COLLECTOR_ALREADY_ACTIVE'
    assert not api.posts


def test_current_stale_queue_retries_once_after_capture_start():
    api=API([queued()])
    result=guard(clock('2026-11-26T08:40:00+07:00'),api)
    assert result['queue_only'] and not result['worker_start_verified'] and not api.posts
    api.runs[0]['updated_at']='2026-11-26T08:49:59+07:00'
    result=guard(clock('2026-11-26T08:50:00+07:00'),api)
    assert result['state']=='MISSED_SESSION_START_DISPATCHED' and len(api.posts)==2
    api=API([queued(),queued(id=2,created_at='2026-11-26T08:50:00+07:00')])
    result=guard(clock('2026-11-26T09:10:00+07:00'),api)
    assert result['stale_queued_run_ids']==[1,2] and not api.posts


def test_unrelated_network_job_does_not_block_data_collector():
    api=API([queued(display_title='SSI network morning',status='in_progress')])
    assert guard(clock('2026-11-26T08:20:00+07:00'),api)['state']=='MISSED_SESSION_START_DISPATCHED'


def test_connection_health_distinguishes_refresh_and_unknown_loss_without_hiding_gaps():
    now=clock('2026-11-26T09:00:00+07:00')
    health=ConnectionHealth(lambda:now)
    health.state('RECONNECTING','TOKEN_REFRESH',1)
    now+=timedelta(seconds=2.5)
    health.state('CONNECTED_WAITING_DATA',None,2)
    health.state('DISCONNECTED','SSI_TIMEOUT',2)
    health.state('FAILED','SSI_TIMEOUT',2)  # One open interruption, two state messages.
    s=health.status()
    assert s['planned_token_refreshes']==s['unexpected_interruptions']==1
    assert s['completed_disconnect_seconds']==2.5 and s['open_interruption']
    assert s['provider_completeness']=='UNKNOWN_NO_PROVIDER_SEQUENCE'
    assert not s['realtime_backfill_available']


def test_windowless_entry_persists_start_finish_and_safe_error(tmp_path):
    def worker(root):
        assert json.loads((root/'report/automation/cloud_dispatch_entry.json').read_text())['state']=='RUNNING'
        return dict(state='CHECKED',providers={'ssi':dict(state='COLLECTOR_ALREADY_ACTIVE')})
    assert entry.main(tmp_path,worker)==0
    result=json.loads((tmp_path/'report/automation/cloud_dispatch_entry.json').read_text())
    assert result['state']=='CHECKED' and result['finished_at_local']
    assert (tmp_path/result['log']).exists()
    def fail(root):raise RuntimeError('private text must not be logged')
    assert entry.main(tmp_path,fail)==1
    assert 'private text' not in (tmp_path/result['log']).read_text()


def test_dispatch_rejects_other_date_before_connecting(tmp_path,monkeypatch):
    import base64
    from scripts import github_ssi_realtime as worker
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'config').mkdir()
    (tmp_path/'config/ssi_github_schedule.json').write_bytes((root/'config/ssi_github_schedule.json').read_bytes())
    for key,value in dict(GITHUB_REPOSITORY=worker.REPOSITORY,GITHUB_REF='refs/heads/main',
                          GITHUB_EVENT_NAME='workflow_dispatch',SSI_REALTIME_STORAGE_APPROVED='true',
                          SSI_REALTIME_ENABLED='true',SSI_API_KEY='fixture',SSI_API_SECRET='fixture',
                          LIQUIDITYLAB_STATE_KEY=base64.b64encode(b'x'*32).decode(),SSI_COLLECTION_DATE='2001-01-01').items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(worker,'install_contract_bundle',lambda *a: None)
    result=worker.run('collect',root=tmp_path)
    assert result['state']=='FAILED_EXPECTED_COLLECTION_DATE_MISMATCH'
    assert not (tmp_path/'data/realtime/ssi').exists()


def test_start_proof_rejects_archived_other_session_and_stale_relay():
    from scripts.check_ssi_start import evaluate
    from scripts.github_ssi_realtime import policy
    p=policy(Path(__file__).resolve().parents[1]);now=clock('2026-11-26T09:05:00+07:00')
    good=dict(state='RECEIVING',active=True,run_id='a'*32,delivery_age_seconds=2,
              first_connected_at='2026-11-26T08:50:02+07:00',quote_counts=dict(ACB=1,FPT=2,HPG=3))
    result=evaluate(good,now,'morning',p)
    assert result['worker_start_verified'] and result['quotes_observed_for_all_symbols'] and not result['late']
    assert not result['market_coverage_verified']
    for changes in (dict(active=False),dict(delivery_age_seconds=40),dict(state='RECONNECTING'),
                    dict(first_connected_at='2026-11-25T08:50:00+07:00')):
        assert not evaluate(dict(good,**changes),now,'morning',p)['worker_start_verified']
    assert evaluate(dict(good,first_connected_at='2026-11-26T09:01:00+07:00'),now,'morning',p)['late']


def test_queued_dispatch_reports_attention_when_start_is_not_verified_after_open(tmp_path):
    from scripts import scheduled_cloud_guard as cloud
    root=Path(__file__).resolve().parents[1]
    (tmp_path/'config').mkdir()
    (tmp_path/'config/ssi_github_schedule.json').write_bytes((root/'config/ssi_github_schedule.json').read_bytes())
    api=API([queued(created_at='2026-11-26T08:59:00+07:00')])
    probe=lambda *a,**kw:dict(state='WORKER_START_NOT_VERIFIED',deadline_passed=True,worker_start_verified=False)
    result=cloud.run(tmp_path,clock('2026-11-26T09:05:00+07:00'),api,
                     refresh=lambda *a:dict(state='CATALOG_PREPARED_AND_ENCRYPTED'),start_check=probe)
    assert result['state']=='ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START'
    assert result['providers']['ssi']['queue_only']


def test_github_guard_also_fails_when_only_a_queued_job_exists_after_open():
    from scripts.ssi_schedule_guard import checked_guard
    api=API([queued(created_at='2026-11-26T08:59:00+07:00')])
    result=checked_guard(clock('2026-11-26T09:05:00+07:00'),api,
        start_check=lambda *a,**kw:dict(state='WORKER_START_NOT_VERIFIED',deadline_passed=True,worker_start_verified=False))
    assert result['state']=='ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START'
    assert result['dispatch_state']=='COLLECTOR_ALREADY_ACTIVE' and not api.posts


def test_connected_without_quotes_for_all_symbols_after_grace_is_not_data_proof():
    from scripts.ssi_schedule_guard import checked_guard
    api=API([queued(status='in_progress')])
    result=checked_guard(clock('2026-11-26T09:05:00+07:00'),api,
        start_check=lambda *a,**kw:dict(worker_start_verified=True,deadline_passed=True,
            data_start_deadline_passed=True,quotes_observed_for_all_symbols=False))
    assert result['state']=='ATTENTION_REQUIRED_MISSED_OR_UNVERIFIED_SSI_START'
