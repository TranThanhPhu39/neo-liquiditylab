from datetime import datetime
import json
from pathlib import Path

import pytest

from scripts import scheduled_cloud_guard as module


class API:
    def __init__(self, runs=(), flags=None):
        self.runs=list(runs);self.flags=flags or {};self.posts=[];self.calls=[]
    def request(self, method, path, payload=None):
        self.calls.append((method,path))
        if method=='POST':self.posts.append((path,payload));return None
        if '/variables/' in path:return {'value':self.flags.get(path.rsplit('/',1)[-1],'true')}
        return {'workflow_runs':self.runs}


@pytest.fixture
def root(tmp_path):
    (tmp_path/'config').mkdir()
    source=Path(__file__).resolve().parents[1]/'config/ssi_github_schedule.json'
    (tmp_path/'config/ssi_github_schedule.json').write_bytes(source.read_bytes())
    return tmp_path


def clock(value='2026-11-18T13:30:00+07:00'):return datetime.fromisoformat(value)


def refresh(root, now):return dict(state='CATALOG_PREPARED_AND_ENCRYPTED')


def test_dispatch_refreshes_before_start_and_targets_only_collection(root):
    api=API();order=[]
    def prepare(root,now):order.append(len(api.posts));return refresh(root,now)
    result=module.dnse_guard(clock(),api,root,prepare)
    assert result['state']=='MISSED_SESSION_START_DISPATCHED' and result['late']
    assert order==[0] and len(api.posts)==1
    path,payload=api.posts[0]
    assert path.endswith('/dnse-realtime.yml/dispatches')
    assert payload['inputs']['mode']=='collect' and payload['inputs']['session']=='afternoon'


@pytest.mark.parametrize('status',['queued','in_progress','waiting','pending','requested'])
def test_active_or_queued_job_is_never_duplicated_or_catalog_refreshed(root,status):
    api=API([dict(id=12,status=status)])
    result=module.dnse_guard(clock(),api,root,lambda *args:pytest.fail('Unnecessary refresh'))
    assert result['state']=='COLLECTOR_ALREADY_ACTIVE' and not api.posts


@pytest.mark.parametrize('value',['2026-11-18T08:30:00+07:00','2026-11-18T11:40:00+07:00',
                                  '2026-11-18T15:07:00+07:00','2026-11-21T13:30:00+07:00'])
def test_no_auth_or_api_calls_outside_dnse_windows(root,value):
    api=API();result=module.dnse_guard(clock(value),api,root,refresh)
    assert result['state'] in ('OUTSIDE_COLLECTION_WINDOW','NON_TRADING_DATE') and api.calls==[]


def test_retry_limit_cooldown_and_smoke_are_separate(root):
    row=dict(id=1,status='completed',conclusion='failure',created_at='2026-11-18T06:10:00Z',
             updated_at='2026-11-18T06:25:00Z',display_title='DNSE collect afternoon')
    assert module.dnse_guard(clock(),API([row]),root,refresh)['state']=='RETRY_COOLDOWN'
    assert module.dnse_guard(clock(),API([row,dict(row,id=2)]),root,refresh)['state']=='RETRY_LIMIT_REACHED'
    smoke=dict(row,display_title='DNSE smoke afternoon',conclusion='success')
    assert module.dnse_guard(clock(),API([smoke]),root,refresh)['state']=='MISSED_SESSION_START_DISPATCHED'


def test_stop_flag_and_disabled_provider_are_respected(root):
    api=API(flags={'DNSE_REALTIME_ENABLED':'false'})
    assert module.dnse_guard(clock(),api,root,refresh)['state']=='COLLECTION_DISABLED' and not api.posts
    (root/'data/control').mkdir(parents=True)
    (root/'data/control/STOP_GITHUB_DISPATCH').touch()
    api=API()
    assert module.run(root,clock(),api,refresh)['state']=='STOPPED' and not api.calls


def test_failure_of_ssi_does_not_prevent_dnse_recovery(root):
    class FailingSSI(API):
        def request(self,method,path,payload=None):
            if '/variables/SSI_' in path:raise RuntimeError('TEST_SSI_UNAVAILABLE')
            return super().request(method,path,payload)
    api=FailingSSI();result=module.run(root,clock(),api,refresh)
    assert result['state']=='FAILED_RESUMABLE'
    assert result['providers']['ssi']['error_code']=='TEST_SSI_UNAVAILABLE'
    assert result['providers']['dnse']['state']=='MISSED_SESSION_START_DISPATCHED'
    assert len(api.posts)==1
    assert (root/'report/automation/cloud_dispatch_status.json').is_file()


def test_catalog_is_refreshed_once_per_source_day_and_flag_restored(root,monkeypatch):
    from scripts import prepare_dnse_realtime
    monkeypatch.setattr(module,'ROOT',root)
    calls=[]
    monkeypatch.setenv('DNSE_REALTIME_STORAGE_APPROVED','original')
    def prepare(publish):calls.append(publish);return dict(state='CATALOG_PREPARED_AND_ENCRYPTED')
    monkeypatch.setattr(prepare_dnse_realtime,'prepare',prepare)
    module.ensure_catalog(root,clock());assert calls==[True]
    import os
    assert os.environ['DNSE_REALTIME_STORAGE_APPROVED']=='original'
    target=root/'report/dnse_ws_catalog_preparation.json';target.parent.mkdir()
    target.write_text(json.dumps(dict(state='CATALOG_PREPARED_AND_ENCRYPTED',as_of_date='2026-11-18',encrypted_release='fixture')))
    assert module.ensure_catalog(root,clock())['state']=='CATALOG_ALREADY_CURRENT'
    assert calls==[True]
    target.write_text(json.dumps(dict(state='CATALOG_PREPARED_AND_ENCRYPTED',as_of_date='2026-11-17',encrypted_release='fixture')))
    module.ensure_catalog(root,clock());assert calls==[True,True]
