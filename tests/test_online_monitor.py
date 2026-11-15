"""Real HTTP relay/replay boundaries and encrypted deployment recovery."""
import base64
from copy import deepcopy
from datetime import datetime,timezone
import gzip
import hashlib
import http.client
import json
from pathlib import Path
import threading

import pytest
pytest.importorskip('pyarrow')

from scripts.serve_monitor_cloud import CloudServer
from src.monitoring.web_relay import RelayLive,validate,RelayPublisher
from src.monitoring.web_archive import encrypt_seed,restore_seed,safe_name,REPOSITORY


def packet():
    now=datetime.now(timezone.utc).isoformat()
    frame=dict(id='id',index=0,time=now,source_time=now,received_at=now,session='SSI_SESSION_UNKNOWN',quality='VALID',units='UNVERIFIED_SSI_QUOTE',
               bids=[['20000',100]],asks=[['20050',100]],features=dict(eligible=False,reason='UNVERIFIED_UNITS'),issues=[])
    record=dict(kind='QUOTE',symbol='ACB',ordinal=0,quote_index=0,received_at=now,connection=1,frame=frame,canonical={},patches=[])
    return dict(manifest=dict(run_id='a'*32,day=now[:10],symbols=['ACB'],created_at=now,
                              unit_contract=dict(status='UNVERIFIED',symbols=[],hash='fixture')),
                status=dict(state='RECEIVING',active=True,connection=1,gaps=0,rejected=0),records=[record])


def test_relay_preserves_receive_order_duplicates_and_restarts_without_reinterpreting_units(tmp_path):
    service=RelayLive(tmp_path);p=packet()
    assert service.apply(p)['next_ordinal']==1
    assert service.apply(p)['next_ordinal']==1 and service.quote_total==1
    view=service.view('a'*32,'ACB')
    assert view['frames'][0]['time']==p['records'][0]['received_at'] and view['unit_status']=='UNVERIFIED'
    service.close();restored=RelayLive(tmp_path)
    assert restored.apply(p)['next_ordinal']==1 and restored.quote_total==1
    second=deepcopy(p);second['records'][0].update(ordinal=1,quote_index=1);second['records'][0]['frame']['index']=1
    assert restored.apply(second)['next_ordinal']==2 and restored.quote_total==2
    missing=deepcopy(second);missing['records'][0]['ordinal']=8
    assert restored.apply(missing)==dict(next_ordinal=2,resync=True)
    restored.close()


def test_relay_rejects_private_fields_scope_escape_and_unordered_records():
    p=packet();p['records'][0]['account']='private'
    with pytest.raises(ValueError,match='PRIVATE'):validate(p)
    p=packet();p['records'][0]['symbol']='OTHER'
    with pytest.raises(ValueError,match='SCOPE'):validate(p)
    p=packet();p['records'].append(dict(p['records'][0],ordinal=4))
    with pytest.raises(ValueError,match='SEQUENCE'):validate(p)


def test_hosted_http_is_private_and_ingest_does_not_grant_browser_or_collection_control(tmp_path):
    password='fixture-web-password-32-characters';token='fixture-ingest-token-32-characters'
    server=CloudServer(tmp_path,port=0,origin='http://127.0.0.1:0',read_password=password,ingest_token=token,bind_host='127.0.0.1')
    server.public_origin='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(method,path,data=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request(method,path,data,headers or {});r=conn.getresponse();result=r.status,dict(r.getheaders()),r.read();conn.close();return result
    basic='Basic '+base64.b64encode(('liquiditylab:'+password).encode()).decode()
    try:
        assert request('GET','/healthz')[0]==200
        assert request('GET','/')[0]==401
        assert request('GET','/api/live/status',headers={'Authorization':'Bearer '+token})[0]==401
        assert request('GET','/realtime',headers={'Authorization':basic})[0]==200
        raw=gzip.compress(json.dumps(packet()).encode())
        headers={'Content-Type':'application/json','Content-Encoding':'gzip','Authorization':'Bearer '+token}
        code,_,body=request('POST','/api/relay',raw,headers);assert code==200 and json.loads(body)['next_ordinal']==1
        _,_,body=request('GET','/api/live/status',headers={'Authorization':basic});status=json.loads(body)
        assert status['hosted'] and status['quotes']==1 and token not in body.decode() and password not in body.decode()
        browser={'Authorization':basic,'Content-Type':'application/json','Origin':server.public_origin,'X-Monitor-CSRF':status['csrf']}
        assert request('POST','/api/live/start','{}',browser)[0]==403
        assert request('POST','/api/live/reviews','{}',browser)[0]==503  # Never fake persistence.
        assert request('GET','/.env',headers={'Authorization':basic})[0]==404
        assert request('GET','/api/live/status',headers={'Authorization':basic,'Host':'evil.example'})[0]==403
        code,_,body=request('GET','/api/live/view?run_id='+'a'*32+'&symbol=ACB',headers={'Authorization':basic})
        assert code==200 and len(json.loads(body)['frames'])==1
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


class FileRelease:
    def __init__(self,folder,names):self.folder=folder;self.names=names
    def request(self,method,path,**kwargs):
        if '/releases/tags/' in path:return dict(assets=[dict(name=n,id=i,size=(self.folder/n).stat().st_size) for i,n in enumerate(self.names)])
        return (self.folder/self.names[int(path.rsplit('/',1)[-1])]).read_bytes()


def test_private_seed_roundtrip_checks_bytes_key_and_path_before_install(tmp_path):
    folder=tmp_path/'source';folder.mkdir();path='data/exports/orderbook-monitor/index.json'
    data=b'{"datasets":[]}'
    (folder/path).parent.mkdir(parents=True);(folder/path).write_bytes(data)
    manifest=dict(format='liquiditylab-private-web-seed-v1',datasets=0,files=[dict(path=path,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())])
    (folder/'seed-manifest.json').write_text(json.dumps(manifest))
    tag='monitor-web-seed-20261115-fixture';key=b'x'*32;encrypted=tmp_path/'encrypted'
    names=encrypt_seed(folder,key,tag,encrypted);api=FileRelease(encrypted,names)
    assert b'datasets' not in b''.join((encrypted/n).read_bytes() for n in names)
    result=restore_seed(tmp_path/'restored',key,tag,api=api)
    assert result['state']=='DECRYPTED_AND_VERIFIED' and (tmp_path/'restored'/path).read_bytes()==data
    with pytest.raises(Exception):restore_seed(tmp_path/'wrong-key',b'y'*32,tag,api=api)
    assert not (tmp_path/'wrong-key'/path).exists()
    for name in ('../../.env','/data/a.json','data/compact/../control/a.json','data/control/.env','data/raw/a.json','data/compact/a\\.json'):
        assert not safe_name(name)


def test_publisher_does_not_forward_unit_evidence_files_and_keeps_ordinal_identity(tmp_path):
    service=RelayLive(tmp_path);p=packet();service.apply(p)
    publisher=RelayPublisher(service,'https://fixture.onrender.com','x'*32)
    outbound=publisher.packet()
    assert outbound['records'][0]['ordinal']==0 and outbound['manifest']['run_id']=='a'*32
    assert 'files' not in outbound['manifest'] and 'evidence' not in outbound['manifest']['unit_contract']
    with pytest.raises(ValueError):RelayPublisher(service,'http://fixture.onrender.com','x'*32)
    service.close()


def test_public_ciphertext_download_uses_no_credential_header_and_is_bounded():
    from scripts.github_api import release_asset_bytes
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'ciphertext'[:limit]
    class Opener:
        def open(self,request,timeout):seen.append(request);return Response()
    class API:
        _token='';_opener=Opener()
        def request(self,*a,**k):raise AssertionError('No REST call for each CDN chunk')
    a=dict(size=10,browser_download_url='https://github.com/owner/repo/releases/download/tag/part.enc')
    assert release_asset_bytes(API(),'owner/repo',a,limit=16)==b'ciphertext'
    assert not seen[0].has_header('Authorization')
    with pytest.raises(ValueError):release_asset_bytes(API(),'owner/repo',dict(a,browser_download_url='https://evil.example/part.enc'),limit=16)
    with pytest.raises(ValueError):release_asset_bytes(API(),'owner/repo',a,limit=2)


def test_hosted_history_streams_parquet_as_gzip_without_loading_all_frames(tmp_path):
    from src.data_pipeline.parquet_replay import write_pack
    package=dict(dataset_id='b'*64,symbol='ACB',day='2026-11-13',frames=[packet()['records'][0]['frame']],event_patches=[])
    relative=write_pack(tmp_path,package)
    export=tmp_path/'data/exports/orderbook-monitor';export.mkdir(parents=True)
    (export/'index.json').write_text(json.dumps(dict(datasets=[dict(dataset_id='b'*64,symbol='ACB',day='2026-11-13',
        file='dnse-acb-2026-11-13.json',compact_folder=relative)])))
    password='fixture-web-password-32-characters'
    server=CloudServer(tmp_path,port=0,origin='http://127.0.0.1:0',read_password=password,ingest_token='x'*32,bind_host='127.0.0.1')
    server.public_origin='http://127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('GET','/api/dataset?dataset_id='+'b'*64,headers={'Authorization':'Basic '+base64.b64encode(('liquiditylab:'+password).encode()).decode()})
        response=conn.getresponse();assert response.status==200 and response.getheader('Content-Encoding')=='gzip'
        assert json.loads(gzip.decompress(response.read()))==package;conn.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
