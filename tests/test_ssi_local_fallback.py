from datetime import datetime
from pathlib import Path
import json

import pytest

from scripts import scheduled_ssi_local as local
from scripts.ssi_checkpoint import pack,unpack
from src.data_pipeline.ssi_live_store import LiveJournal,atomic

DAY='2026-11-26'
NOW=datetime.fromisoformat(DAY+'T12:47:00+07:00')
CAPTURE='a'*32


@pytest.fixture
def root(tmp_path):
    (tmp_path/'config').mkdir()
    source=Path(__file__).resolve().parents[1]/'config/ssi_github_schedule.json'
    (tmp_path/'config/ssi_github_schedule.json').write_bytes(source.read_bytes())
    return tmp_path


class Service:
    def __init__(self,root):self.root=root;self.journal=None;self.ticks=0
    def start(self,symbols,seconds):
        assert 10<=seconds<=21600
        self.journal=LiveJournal(self.root,symbols,folder=self.root/'data/realtime/ssi'/DAY/CAPTURE)
        self.journal.run_id=CAPTURE;self.journal.manifest['run_id']=CAPTURE
        for symbol in symbols:
            self.journal.append(dict(kind='QUOTE',symbol=symbol,received_at=NOW.isoformat(),quote_index=0))
            self.journal.append(dict(kind='TRADE',symbol=symbol,received_at=NOW.isoformat()))
        self.thread=self
    def is_alive(self):return self.ticks<2
    def join(self,timeout):
        self.ticks+=1
        if self.ticks==2:self.journal.close('STOPPED')
    def status(self):
        return dict(state='STOPPED' if self.ticks==2 else 'RECEIVING',active=self.ticks<2,
            quotes=3,trades=3,quote_counts=dict(ACB=1,FPT=1,HPG=1),trade_counts=dict(ACB=1,FPT=1,HPG=1),
            rejected=0,gaps=0,first_connected_at=NOW.isoformat(),reason='DURATION_LIMIT' if self.ticks==2 else None)
    def stop(self):self.ticks=2
    def close(self):
        if not self.journal.handle.closed:self.journal.close('STOPPED')


class Store:
    def __init__(self):self.calls=[]
    def publish(self,service,session,worker,attempt,final=False):
        self.calls.append((session,final))
        assets=pack(service.journal.folder,service.journal.manifest,service.status(),b'x'*32,'fixture')
        _,proof=unpack(assets,b'x'*32,'fixture')
        assert proof['records']==6
        return dict(tag='ssi-ws-fixture-local-123456',verification=proof,final=final)


def missing(*a,**kw):return dict(worker_start_verified=False,deadline_passed=False)


def test_local_capture_when_cloud_missing_keeps_separate_encrypted_archive(root):
    store=Store()
    result=local.run(root=root,clock=lambda:NOW,probe=missing,service_factory=Service,
                     store_factory=lambda root:store,approval=lambda:True)
    assert result['state']=='LOCAL_CAPTURE_PROCESSED'
    assert result['collection_state']=='COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED'
    assert not result['relay_enabled'] and not result['market_coverage_verified']
    assert result['capture_run_id']==CAPTURE
    assert store.calls==[('afternoon-local',True)]
    assert local.run(root=root,clock=lambda:NOW,probe=missing)['state']=='LOCAL_SESSION_ALREADY_RECORDED'


def test_healthy_cloud_skips_local_collector_and_does_not_duplicate_live_writer(root):
    proof=lambda *a,**kw:dict(worker_start_verified=True,deadline_passed=False)
    result=local.run(root=root,clock=lambda:NOW,probe=proof,
                     service_factory=lambda root:pytest.fail('Unnecessary duplicate'),approval=lambda:pytest.fail('No auth needed'))
    assert result['state']=='REMOTE_WORKER_HEALTHY'
    assert not (root/'data/realtime').exists()


def test_upload_outage_preserves_raw_then_recovers_after_session_without_reopening_journal(root):
    def offline(root):raise RuntimeError('GITHUB_HTTP_503')
    result=local.run(root=root,clock=lambda:NOW,probe=missing,service_factory=Service,
                     store_factory=offline,approval=lambda:True)
    assert result['state']=='LOCAL_CAPTURE_SAVED_UPLOAD_PENDING'
    folder=root/'data/realtime/ssi'/DAY/CAPTURE
    before={p.name:p.read_bytes() for p in folder.glob('*.parquet')}
    recovered=local.run(root=root,clock=lambda:NOW.replace(hour=15,minute=12),store_factory=lambda root:Store())
    assert recovered['state']=='PENDING_LOCAL_UPLOAD_RECOVERED'
    assert {p.name:p.read_bytes() for p in folder.glob('*.parquet')}==before


def test_local_authorization_survives_unavailable_github_and_respects_explicit_disable(root):
    def unavailable():raise RuntimeError('GITHUB_AUTH_NOT_AVAILABLE')
    result=local.run(root=root,clock=lambda:NOW,probe=missing,service_factory=Service,
                     store_factory=lambda root:Store(),approval=unavailable)
    assert result['state']=='LOCAL_CAPTURE_PROCESSED' and not result['cloud_approval_checked']


def test_local_respects_stop_and_lunch_and_preopen_grace(root):
    for now in (NOW.replace(hour=8,minute=51),NOW.replace(hour=11,minute=40),NOW.replace(hour=12,minute=46)):
        assert local.run(root=root,clock=lambda:now,approval=lambda:pytest.fail('Out of window'))['state']=='OUTSIDE_LOCAL_FALLBACK_WINDOW'
    (root/'data/control/STOP_SSI_LOCAL').touch()
    assert local.run(root=root,clock=lambda:NOW)['state']=='STOPPED_BY_LOCAL_FLAG'


def test_repository_disable_prevents_local_start_when_available(root):
    result=local.run(root=root,clock=lambda:NOW,probe=missing,approval=lambda:False,
                     service_factory=lambda root:pytest.fail('Disabled'))
    assert result['state']=='COLLECTION_DISABLED_BY_REPOSITORY'
