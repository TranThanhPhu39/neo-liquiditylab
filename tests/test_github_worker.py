import base64
import hashlib
import json
from pathlib import Path
from urllib.request import Request

import pytest
from cryptography.exceptions import InvalidTag

from scripts.github_api import SafeRedirect
from scripts.github_checkpoint import pack_state,unpack_state,state_key
from scripts import github_worker


def test_checkpoint_encrypts_and_roundtrips_without_auth_state(tmp_path):
    key=b'x'*32
    source=tmp_path/'source.db'
    source.write_bytes(b'PRIVATE_MARKET_STATE_EXAMPLE')
    status=tmp_path/'status.json'
    status.write_text('{"state":"RUNNING"}')
    folder=tmp_path/'ciphertext'
    pack_state({'snapshot.db':source,'status.json':status},folder,key,'repo:tag')
    assert all(b'PRIVATE_MARKET_STATE_EXAMPLE' not in p.read_bytes() for p in folder.glob('*.enc'))
    names=unpack_state(folder,tmp_path/'restore',key,'repo:tag')
    assert names=={'snapshot.db','status.json'}
    assert (tmp_path/'restore/snapshot.db').read_bytes()==source.read_bytes()
    with pytest.raises(ValueError,match='UNAPPROVED'):
        pack_state({'.env':status},tmp_path/'bad',key,'repo:tag')


def test_checkpoint_rejects_wrong_key_context_and_tampering(tmp_path):
    source=tmp_path/'db'
    source.write_bytes(b'fixture')
    folder=tmp_path/'enc'
    key=b'x'*32
    pack_state({'snapshot.db':source},folder,key,'repo:tag')
    with pytest.raises(InvalidTag):
        unpack_state(folder,tmp_path/'wrongkey',b'y'*32,'repo:tag')
    with pytest.raises(ValueError,match='MANIFEST'):
        unpack_state(folder,tmp_path/'wrongcontext',key,'another:repo')
    part=next(folder.glob('*.enc'))
    part.write_bytes(part.read_bytes()+b'tamper')
    with pytest.raises(ValueError,match='HASH'):
        unpack_state(folder,tmp_path/'tampered',key,'repo:tag')


def test_state_key_requires_exactly_256_bits():
    assert state_key(base64.b64encode(b'x'*32).decode())==b'x'*32
    for invalid in ('not_base64',base64.b64encode(b'x'*16).decode()):
        with pytest.raises(ValueError):
            state_key(invalid)


def test_cross_host_redirect_removes_github_authorization():
    request=Request('https://api.github.com/a',headers={'Authorization':'Bearer synthetic-secret'})
    redirected=SafeRedirect().redirect_request(request,None,302,'Found',{},'https://release-assets.githubusercontent.com/file')
    assert redirected.get_header('Authorization') is None
    with pytest.raises(ValueError):
        SafeRedirect().redirect_request(request,None,302,'Found',{},'http://example.com/file')


def test_github_entry_requires_expected_repo_ref_and_api_secret(monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY','attacker/fork')
    monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    monkeypatch.setattr(github_worker,'get_vnstock_api_key',lambda:'fixture-key')
    with pytest.raises(RuntimeError,match='UNEXPECTED'):
        github_worker.require_environment()
    monkeypatch.setenv('GITHUB_REPOSITORY',github_worker.REPOSITORY)
    monkeypatch.setattr(github_worker,'get_vnstock_api_key',lambda:None)
    with pytest.raises(RuntimeError,match='SECRET_REQUIRED'):
        github_worker.require_environment()


def test_preflight_uses_diagnostic_namespace_without_collection(tmp_path,monkeypatch):
    monkeypatch.setattr(github_worker,'ROOT',tmp_path)
    monkeypatch.delenv('LIQUIDITYLAB_STATE_KEY',raising=False)
    monkeypatch.setattr(github_worker,'require_environment',lambda:None)
    calls=[]
    def request(operation,**kwargs):
        calls.append((operation,kwargs))
        if operation=='universe':
            return {'rows':[{'symbol':'FPT'}]}
        return {'market_request_succeeded':True,'missing_columns':[],
                'rows':[{'symbol':s} for s in ('FPT','HPG','ACB')],
                'received_at_utc':'2026-10-28T07:00:00+00:00','version':'5.2.1','account':{'authenticated':True}}
    monkeypatch.setattr(github_worker,'sdk_request',request)
    github_worker.preflight()
    report=json.loads((tmp_path/'report/github/preflight.json').read_text())
    assert report['status']=='PASSED' and report['collection_history_added'] is False
    assert len(calls)==2 and not (tmp_path/'data').exists()


def test_workflows_do_not_collect_on_push_or_pull_request():
    import yaml
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/collect.yml').read_text(),Loader=yaml.BaseLoader)
    assert set(workflow['on'])=={'schedule','workflow_dispatch'}
    job=workflow['jobs']['collector']
    assert job['runs-on']=='ubuntu-24.04' and int(job['timeout-minutes'])<360
    assert 'LIQUIDITYLAB_WORKER_ENABLED' in job['if']
    assert workflow['concurrency']['cancel-in-progress']=='false'


def test_sdk_diagnostic_keeps_safe_error_code_without_raw_message():
    from scripts.market_preflight import SDKRequestError
    error=SDKRequestError({'error':'EntitlementError','error_code':'VNSTOCK_AUTH_DEVICE_LIMIT','message':'private-value'})
    assert error.category=='EntitlementError' and error.code=='VNSTOCK_AUTH_DEVICE_LIMIT'
    assert 'private-value' not in str(error)
    invalid=SDKRequestError({'error':'private-value: auth header','error_code':'unsafe private-value'})
    assert invalid.category=='UNKNOWN_SDK_ERROR' and invalid.code is None


def test_log_monitor_excludes_unapproved_secret_objects():
    from scripts.github_monitor import summarize_log
    result=summarize_log('2026-10-28 {"status":"FAILED","sdk_error_code":"VNSTOCK_AUTH_DEVICE_LIMIT"}\n'
                         '{"token":"synthetic-private-value"}\n128 passed in 2.59s')
    assert len(result['worker_summaries'])==1 and len(result['pytest_totals'])==1
    assert 'synthetic-private-value' not in json.dumps(result)


def test_dnse_monitor_keeps_counts_without_raw_quota_paths_or_page_tokens():
    from scripts.github_monitor import summarize_log
    log=json.dumps({'provider':'dnse','status':'HTTP_JSON_PROBE_COMPLETED_NOT_UNIT_VERIFIED',
        'units_verified':False,'finance_enabled':False,'samples':[{'symbol':'FPT','quote_count':2,
        'artifact':'private-local-path','rate_limit_headers':{'X-RateLimit-Remaining':99999},
        'nextPageToken':'private-page-token','quote_shapes':[{'symbol':'FPT','bid_levels':3,'ask_levels':3}]}]})
    result=summarize_log(log)
    summary=result['worker_summaries'][0]
    assert summary['samples'][0]['quote_count']==2 and summary['mode']=='latest'
    assert 'private-' not in json.dumps(result) and '99999' not in json.dumps(result)


def test_scope_probe_runs_one_bounded_diagnostic_sweep(tmp_path,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(github_worker,'ROOT',tmp_path)
    monkeypatch.setattr(github_worker,'require_environment',lambda:None)
    monkeypatch.setattr(github_worker,'sdk_request',lambda op:{'rows':[{'symbol':'FPT'}]})
    calls=[]
    monkeypatch.setattr(github_worker.subprocess,'run',lambda args,**kwargs:calls.append(args) or SimpleNamespace(returncode=0))
    github_worker.scope_probe()
    assert len(calls)==1 and '--all-batches' in calls[0]
    assert (tmp_path/'data/raw/github_scope_universe.json').is_file()
    assert not (tmp_path/'data/github').exists()
