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
from scripts.github_api import GitHubError
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


def test_multi_part_checkpoint_streaming_preserves_bytes_and_size_guards(tmp_path,monkeypatch):
    import scripts.ssi_checkpoint as checkpoint
    monkeypatch.setattr(checkpoint,'CHUNK',128)
    j=capture(tmp_path);assets=pack(j.folder,j.manifest,dict(state='STOPPED'),b'x'*32,'multi-part')
    assert len(assets)>2
    files,proof=unpack(assets,b'x'*32,'multi-part')
    assert proof['records']==2
    for part in j.manifest['files']:
        assert files['run/'+part['path']]==(j.folder/part['path']).read_bytes()
    with pytest.raises(ValueError,match='SIZE_LIMIT'):unpack(assets,b'x'*32,'multi-part',max_bytes=1)


def test_collect_guard_requires_approval_and_secrets_before_source_access(tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY','TranThanhPhu39/neo-liquiditylab');monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    monkeypatch.delenv('SSI_REALTIME_STORAGE_APPROVED',raising=False)
    with pytest.raises(ValueError,match='APPROVAL'):run('smoke',root=tmp_path)
    monkeypatch.setenv('SSI_REALTIME_STORAGE_APPROVED','true');monkeypatch.delenv('SSI_REALTIME_ENABLED',raising=False)
    with pytest.raises(ValueError,match='NOT_ENABLED'):run('collect',root=tmp_path)


def test_workflow_has_separate_crons_guarded_modes_and_no_raw_artifact_upload():
    text=(ROOT/'.github/workflows/ssi-realtime.yml').read_text()
    assert "19 1 * * 1-5" in text and "12 5 * * 1-5" in text
    guard=(ROOT/'.github/workflows/ssi-schedule-guard.yml').read_text()
    assert "24,34,44,54 1 * * 1-5" in guard
    assert 'SSI_COLLECTION_DATE' in text and 'collection_date:' in text
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
        if method=='PATCH':
            self.releases[int(path.rsplit('/',1)[-1])].update(payload);return {}
        if method=='GET' and '/releases/tags/' in path:
            for r in self.releases.values():
                if r['tag_name']==path.rsplit('/',1)[-1]:return r
            raise GitHubError(404)
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


def test_import_cloud_capture_does_not_overwrite_local_runs(tmp_path):
    from scripts.restore_ssi_checkpoint import install
    j=capture(tmp_path/'source');assets=pack(j.folder,j.manifest,{'state':'STOPPED'},b'x'*32,'test')
    files,_=unpack(assets,b'x'*32,'test');root=tmp_path/'destination'
    destination=Path(install(files,root=root))
    assert json.loads((destination/'manifest.json').read_bytes())['state']=='IMPORTED_CLOUD_CHECKPOINT'
    assert destination.parent.parent==root/'data/realtime/ssi'
    with pytest.raises(ValueError,match='NO_OVERWRITE'):install(files,root=root)

def test_cli_prints_a_closed_window_failure_instead_of_only_exit_one(monkeypatch,capsys):
    import scripts.github_ssi_realtime as worker
    monkeypatch.setattr('sys.argv',['github_ssi_realtime','--mode','collect','--session','morning'])
    monkeypatch.setattr(worker,'run',lambda *args:dict(state='FAILED_SESSION_WINDOW_ALREADY_CLOSED',realtime_recovered=False))
    assert worker.main()==1
    import json
    assert json.loads(capsys.readouterr().out)['state']=='FAILED_SESSION_WINDOW_ALREADY_CLOSED'


def test_market_topics_capture_every_equity_board_and_skip_nonstocks():
    from src.data_providers.base import ProviderError
    from src.data_providers.ssi_readonly import SSIReadOnly
    from src.data_providers.ssi_stream import market_record, requests

    catalog_client = SSIReadOnly('test-key', 'test-secret')
    securities = {'HOSE':'ACB','HNX':'SHS','UPCOM':'A32'}
    catalog_client._securities = lambda params: [
        dict(symbol=securities[params['board']], board=params['board'], stockType='Stock'),
        dict(symbol='NOT AN EQUITY', board='DERIVATIVES', stockType='Future')]
    catalog = catalog_client.stock_catalog()
    assert catalog == {symbol:board for board,symbol in securities.items()}
    subscriptions = {request['topics'][0] for request in requests(['ACB'],market=True)}
    assert {topic for topic in subscriptions if topic.startswith(('trade.','quote.'))} == {
        f'{kind}.{board.lower()}@tick' for kind in ('trade','quote') for board in securities}
    for board,symbol in securities.items():
        packet = dict(channel='DATA',topic=f'quote.{board.lower()}@tick',
                      data=dict(s=symbol,t='2026/11/27 09:20:00',bids=[['20','100']],asks=[]))
        row = market_record(packet,['ACB'],catalog)
        assert row['exchange'] == board and row['symbol'] == symbol and row['source_topic'] == packet['topic']
        assert row['payload']['bids'] == [['20','100']]
    assert market_record(dict(packet,data=dict(s='NOT AN EQUITY')),['ACB'],catalog) is None
    with pytest.raises(ProviderError,match='SSI_TOPIC_SCOPE_MISMATCH'):
        market_record(dict(packet,topic='quote.hose@tick'),['ACB'],catalog)
    with pytest.raises(ProviderError,match='SSI_UNREQUESTED_PRIVATE_CHANNEL'):
        market_record(dict(packet,channel='TRADING'),['ACB'],catalog)


def test_market_segments_are_recoverable_and_separate_from_monitor_checkpoints(tmp_path):
    from scripts.restore_ssi_checkpoint import install
    from src.data_pipeline.ssi_market_archive import SSIMarketArchive
    from src.data_pipeline.ssi_live_store import read_records

    timestamp = [0]
    market = SSIMarketArchive(tmp_path,{'ACB':'HOSE','A32':'UPCOM'},segment_seconds=10,clock=lambda:timestamp[0])
    row = dict(kind='QUOTE',symbol='A32',exchange='UPCOM',received_at='2026-11-27T02:00:00+00:00',
               payload={'bids':[['20000','100']]})
    market.receive(row)
    assert market.take_sealed() is None
    timestamp[0] = 11
    market.receive(dict(row,kind='TRADE',symbol='ACB',exchange='HOSE',payload={'p':'21000','q':'500'}))
    store = SSICheckpoints('owner/repo',b'x'*32,api=ReleaseAPI())
    for segment_index in (1,2):
        segment = market.take_sealed(final=segment_index==2)
        proof = store.publish(segment,'morning','123','1',final=True)
        assert proof['tag'].startswith('ssi-market-2026-11-27-morning-')
        assert proof['verification']['records'] == 1
        assert store.publish(segment,'morning','123','1',final=True) == proof
        files, _ = store.restore(proof['tag'])
        folder = Path(install(files,root=tmp_path/'restored'))
        assert folder.parent.parent.name == 'ssi-market'
        if segment_index==1:
            assert read_records(folder,'A32','QUOTE')[0]['payload']==row['payload']
    assert not store.api.deleted
    counts = market.status()['boards']
    assert counts['UPCOM']['symbols_with_quotes']==counts['HOSE']['symbols_with_trades']==1
