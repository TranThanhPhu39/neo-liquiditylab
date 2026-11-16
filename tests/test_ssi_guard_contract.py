from datetime import datetime
import hashlib
import json

import pytest

from scripts.ssi_schedule_guard import guard
from src.data_pipeline.ssi_contract_bundle import install_contract_bundle
from src.data_pipeline.ssi_live_store import encoded


class API:
    def __init__(self,runs=()):self.runs=list(runs);self.posts=[]
    def request(self,method,path,payload=None):
        if method=='POST':self.posts.append(payload);return None
        if '/variables/' in path:return {'value':'true'}
        return {'workflow_runs':self.runs}


def test_guard_dispatches_missing_session_but_never_duplicates_active_or_queued_runs():
    now=datetime.fromisoformat('2026-11-16T13:50:00+07:00');api=API()
    assert guard(now,api)['state']=='MISSED_SESSION_START_DISPATCHED' and len(api.posts)==1
    assert api.posts[0]['inputs']['session']=='afternoon'
    api=API([dict(id=1,status='queued')]);assert guard(now,api)['state']=='COLLECTOR_ALREADY_ACTIVE' and not api.posts
    assert guard(datetime.fromisoformat('2026-11-16T15:11:00+07:00'),API())['state']=='OUTSIDE_COLLECTION_WINDOW'
    assert guard(datetime.fromisoformat('2026-11-15T13:50:00+07:00'),API())['state']=='NON_TRADING_DATE'


def test_guard_caps_retries_and_cools_down_failures():
    now=datetime.fromisoformat('2026-11-16T13:50:00+07:00')
    run=dict(id=1,status='completed',conclusion='failure',created_at='2026-11-16T13:40:00+07:00',updated_at='2026-11-16T13:45:00+07:00')
    assert guard(now,API([run]))['state']=='RETRY_COOLDOWN'
    assert guard(now,API([run,dict(run,id=2)]))['state']=='RETRY_LIMIT_REACHED'
    assert guard(now,API([dict(run,conclusion='success')]))['state']=='SESSION_ALREADY_COMPLETED'


def test_runner_guard_uses_workflow_flags_without_requesting_repository_variable_api():
    class RunnerAPI(API):
        def request(self,method,path,payload=None):
            assert '/variables/' not in path
            return super().request(method,path,payload)
    flags={'SSI_REALTIME_ENABLED':'true','SSI_REALTIME_STORAGE_APPROVED':'true'}
    assert guard(datetime.fromisoformat('2026-11-16T13:50:00+07:00'),RunnerAPI(),flags=flags)['state']=='MISSED_SESSION_START_DISPATCHED'
    assert guard(datetime.fromisoformat('2026-11-16T13:50:00+07:00'),RunnerAPI(),flags={})['state']=='COLLECTION_DISABLED'


def test_contract_bundle_validates_evidence_before_changing_live_contract(tmp_path):
    name='data/raw/ssi/unit_certificate_fixture.json';value={'fixture':'unit-proof'}
    contract=dict(status='EMPIRICALLY_VERIFIED',symbols=['ACB'],quote_semantics='FULL_SNAPSHOT',
        price_multiplier='1',volume_multiplier='1',evidence=[dict(path=name,sha256=hashlib.sha256(encoded(value)).hexdigest())])
    bundle=dict(format='liquiditylab-ssi-unit-contract-bundle-v1',contract=contract,evidence_files={name:value})
    assert install_contract_bundle(tmp_path,json.dumps(bundle))['symbols']==['ACB']
    original=(tmp_path/'data/control/ssi_live_contract.json').read_bytes()
    bundle['evidence_files'][name]={'changed':True}
    with pytest.raises(ValueError,match='EVIDENCE'):install_contract_bundle(tmp_path,json.dumps(bundle))
    assert (tmp_path/'data/control/ssi_live_contract.json').read_bytes()==original
    bundle['evidence_files']={'../../.env':value}
    with pytest.raises(ValueError,match='UNSAFE'):install_contract_bundle(tmp_path,json.dumps(bundle))
