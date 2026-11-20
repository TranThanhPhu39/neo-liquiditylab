"""Recovery cannot retain decrypted payloads between jobs or overlap heavy views."""
import json
import threading
from types import SimpleNamespace

import pytest

from src.monitoring import web_archive as archive


@pytest.mark.parametrize('mode',['install','active','failed'])
def test_capture_recovery_releases_payload_on_success_skip_and_failure(tmp_path,monkeypatch,mode):
    run='a'*32;tag='ssi-ws-20261120-fixture'
    files={'run/manifest.json':json.dumps(dict(run_id=run,last_ordinal=0)).encode(),'run/part-00000000.parquet':b'payload'}
    class Checkpoints:
        def __init__(self,*args,**kwargs):pass
        def restore(self,*args,**kwargs):return files,dict(run_id=run)
    monkeypatch.setattr(archive,'SSICheckpoints',Checkpoints)
    installed=[]
    def install(value,**kwargs):
        assert value['run/part-00000000.parquet']==b'payload'
        if mode=='failed':raise ValueError('FIXTURE_INSTALL_FAILED')
        installed.append(True)
    monkeypatch.setattr(archive,'install',install)
    live=SimpleNamespace(lock=threading.RLock(),journal=SimpleNamespace(run_id=run),status=lambda:dict(active=True)) if mode=='active' else None
    recovery=archive.CloudRecovery(tmp_path,b'x'*32,api=object(),live=live)
    if mode=='failed':
        with pytest.raises(ValueError,match='FIXTURE_INSTALL_FAILED'):recovery.restore_capture(tag)
    else:recovery.restore_capture(tag)
    assert files=={}
    assert bool(installed)==(mode=='install')
    assert (tag in recovery.known_tags)==(mode=='install')


def test_background_checkpoint_restore_uses_the_same_budget_as_heavy_http(tmp_path,monkeypatch):
    lock=threading.BoundedSemaphore(1);installed=threading.Event();files={'run/manifest.json':b'{}'}
    class API:
        def request(self,*args):return [dict(tag_name='ssi-ws-20261120-fixture')]
    class Checkpoints:
        def __init__(self,*args,**kwargs):pass
        def restore(self,*args,**kwargs):
            acquired=lock.acquire(blocking=False)
            if acquired:lock.release()
            assert not acquired
            return files,dict(run_id='a'*32)
    monkeypatch.setattr(archive,'SSICheckpoints',Checkpoints)
    def install(*args,**kwargs):installed.set();recovery.closed.set()
    monkeypatch.setattr(archive,'install',install)
    recovery=archive.CloudRecovery(tmp_path,b'x'*32,api=API(),resource_lock=lock)
    recovery.start()
    try:assert installed.wait(3)
    finally:recovery.close();recovery.thread.join(timeout=3)
    assert not recovery.thread.is_alive() and files=={}
    assert lock.acquire(blocking=False);lock.release()
