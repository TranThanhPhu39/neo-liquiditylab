import base64
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs,urlparse

import pytest
pytest.importorskip('pyarrow')

from scripts.github_ssi_realtime import plan,outcome,policy,run
from scripts.ssi_checkpoint import pack,unpack,SSICheckpoints
from src.data_pipeline.ssi_live_store import LiveJournal

ROOT=Path(__file__).resolve().parents[1]


def test_session_windows_do_not_treat_late_or_closed_as_success():
    p=policy(ROOT)
    morning=plan(datetime.fromisoformat('2026-11-13T08:37:00+07:00'),'morning',p)
    assert not morning['late'] and not morning['closed'] and morning['capture_from'].endswith('08:50:00+07:00')
    assert plan(datetime.fromisoformat('2026-11-13T11:36:00+07:00'),'morning',p)['closed']
    assert plan(datetime.fromisoformat('2026-11-13T13:10:00+07:00'),'afternoon',p)['late']
    assert plan(datetime.fromisoformat('2026-11-14T09:00:00+07:00'),'morning',p)['non_trading_date']
    with pytest.raises(ValueError):plan(datetime(2026,11,13),'morning',p)


def test_outcomes_require_data_and_do_not_hide_gaps_or_quarantine():
    s=dict(state='STOPPED',quotes=10,trades=2,quote_counts={'ACB':1,'FPT':2,'HPG':7},rejected=0,gaps=0,first_connected_at='2026-11-13T01:50:00+00:00')
    w=plan(datetime.fromisoformat('2026-11-13T08:50:00+07:00'),'morning',policy(ROOT))
    assert outcome(s,w)=='COLLECTED_ENCRYPTED_AND_RESTORE_VERIFIED'
    assert outcome(dict(s,first_connected_at='2026-11-13T02:01:00+00:00'),w)=='PARTIAL_LATE_START'
    assert outcome(dict(s,gaps=1),w)=='PARTIAL_CONNECTION_GAPS'
    assert outcome(dict(s,rejected=1),w)=='FAILED_DATA_QUARANTINE'
    assert outcome(dict(s,trades=0),None)=='FAILED_NO_COMPLETE_MARKET_DATA_PROOF'
    assert outcome(dict(s,quote_counts={'ACB':1}),None)=='FAILED_INCOMPLETE_SYMBOLS'


def capture(tmp_path):
    j=LiveJournal(tmp_path,['ACB'])
    j.append(dict(kind='QUOTE',symbol='ACB',quote_index=0,received_at='2026-11-13T02:00:00+00:00',payload={'s':'ACB','bids':[['20350','0']], 'asks':None}))
    j.append(dict(kind='TRADE',symbol='ACB',received_at='2026-11-13T02:00:00+00:00',trade={'id':'one','qty':100}))
    j.close();return j


def test_encrypted_parquet_roundtrip_keeps_all_bytes_no_plaintext_market_assets(tmp_path):
    j=capture(tmp_path);key=b'x'*32;context='repo:ssi-test'
    assets=pack(j.folder,j.manifest,{'state':'STOPPED'},key,context)
    assert all(name=='manifest.json' or name.endswith('.enc') for name in assets)
    assert b'20350' not in b''.join(assets.values())
    files,proof=unpack(assets,key,context)
    assert proof['records']==2 and proof['quote_counts']['ACB']==1 and proof['trade_counts']['ACB']==1
    for p in j.manifest['files']:assert files['run/'+p['path']]==(j.folder/p['path']).read_bytes()
    with pytest.raises(Exception):unpack(assets,b'y'*32,context)
    with pytest.raises(ValueError):unpack(assets,key,'wrong-context')
    damaged=dict(assets);damaged['part-0000.enc']=damaged['part-0000.enc'][:-1]+b'!'
    with pytest.raises(ValueError,match='HASH'):unpack(damaged,key,context)


def test_only_committed_allowlisted_parquet_can_enter_checkpoint(tmp_path):
    j=capture(tmp_path);manifest=deepcopy(j.manifest);manifest['files'][0]['path']='../../.env'
    with pytest.raises(ValueError,match='UNSAFE'):pack(j.folder,manifest,{},b'x'*32,'context')
    p=j.folder/j.manifest['files'][0]['path'];p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='CHANGED'):pack(j.folder,j.manifest,{},b'x'*32,'context')


def test_collect_guard_requires_approval_and_secrets_before_source_access(tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY','TranThanhPhu39/neo-liquiditylab');monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    monkeypatch.delenv('SSI_REALTIME_STORAGE_APPROVED',raising=False)
    with pytest.raises(ValueError,match='APPROVAL'):run('smoke',root=tmp_path)
    monkeypatch.setenv('SSI_REALTIME_STORAGE_APPROVED','true');monkeypatch.delenv('SSI_REALTIME_ENABLED',raising=False)
    with pytest.raises(ValueError,match='NOT_ENABLED'):run('collect',root=tmp_path)


def test_workflow_has_separate_crons_guarded_modes_and_no_raw_artifact_upload():
    text=(ROOT/'.github/workflows/ssi-realtime.yml').read_text()
    assert "37 1 * * 1-5" in text and "37 5 * * 1-5" in text
    assert 'SSI_REALTIME_ENABLED' in text and 'SSI_REALTIME_STORAGE_APPROVED' in text
    assert 'upload-artifact' not in text and 'secrets.SSI_API_SECRET' in text
    network=text.split('  network:',1)[1].split('  collector:',1)[0]
    assert 'secrets.' not in network and 'SMOKE_SECONDS' in text


class ReleaseAPI:
    def __init__(self):
        self.releases={};self.assets={};self.deleted=[];self.break_download=False

    def request(self,method,path,payload=None,**kwargs):
        if method=='POST' and path.endswith('/releases'):
            n=len(self.releases)+1
            r=dict(id=n,upload_url=f'https://uploads.github.com/test/{n}'+'{?name}',assets=[],**payload)
            self.releases[n]=r;return r
        if method=='POST' and path.startswith('https://uploads.github.com/test/'):
            parsed=urlparse(path);n=int(parsed.path.rsplit('/',1)[-1]);name=parse_qs(parsed.query)['name'][0]
            aid=len(self.assets)+1;data=kwargs['data'];self.assets[aid]=data
            self.releases[n]['assets'].append(dict(id=aid,name=name,size=len(data)));return {}
        if method=='PATCH':return {}
        if method=='GET' and '/releases/tags/' in path:
            return next(r for r in self.releases.values() if r['tag_name']==path.rsplit('/',1)[-1])
        if method=='GET' and '/releases/assets/' in path:
            data=self.assets[int(path.rsplit('/',1)[-1])]
            return data+b'broken' if self.break_download else data
        if method=='DELETE':self.deleted.append(path);return None
        raise AssertionError((method,path))


def test_remote_checkpoint_download_is_verified_before_own_temporary_release_is_deleted(tmp_path):
    j=capture(tmp_path);api=ReleaseAPI();store=SSICheckpoints('owner/repo',b'x'*32,api=api)
    class Service:
        lock=threading.RLock()
        journal=j
        def status(self):return dict(state='STOPPED',csrf='must-not-be-published')
    service=Service()
    temporary=store.publish(service,'morning','123','1')
    assert temporary['verification']['records']==2 and not api.deleted
    api.break_download=True
    with pytest.raises(Exception):store.publish(service,'morning','123','1',final=True)
    assert not api.deleted
    api.break_download=False
    final=store.publish(service,'morning','123','1',final=True)
    assert final['verification']['state']=='DOWNLOADED_DECRYPTED_PARQUET_VERIFIED'
    assert len(api.deleted)==2 and temporary['tag'] in api.deleted[-1]
    files,_=store.restore(final['tag'])
    assert 'csrf' not in json.loads(files['status.json'])
