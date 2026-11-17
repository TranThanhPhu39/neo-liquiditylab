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
from src.monitoring.public_releases import PublicReleases
from src.monitoring.web_archive import ssi_recovery_tags


def test_ssi_recovery_keeps_saved_captures_when_market_releases_fill_the_feed():
    saved=['ssi-ws-20261116-fixture-'+str(i) for i in range(6)]
    feed=[dict(tag_name='monitor-web-seed-market-20261116-'+str(i)) for i in range(10)]
    assert ssi_recovery_tags(feed,saved)==saved
    feed.insert(0,dict(tag_name='ssi-ws-20261117-new-fixture'))
    assert ssi_recovery_tags(feed,saved)==['ssi-ws-20261117-new-fixture']+saved[:5]


def test_ssi_recovery_rejects_arbitrary_release_paths_and_unbounded_lists():
    import pytest
    with pytest.raises(ValueError):ssi_recovery_tags([],['../../.env'])
    with pytest.raises(ValueError):ssi_recovery_tags([],['ssi-ws-20261116-fixture']*7)


def test_cold_start_status_and_health_do_not_load_the_market_catalog(tmp_path):
    from types import SimpleNamespace
    server=CloudServer(tmp_path,port=0,origin='http://127.0.0.1:0',read_password='p'*32,ingest_token='x'*32,bind_host='127.0.0.1')
    server.public_origin='http://127.0.0.1:'+str(server.server_port)
    server.recovery=SimpleNamespace(seed_ready=False,state='RECOVERING',close=lambda:None)
    server.index=lambda:(_ for _ in ()).throw(AssertionError('Catalog read during recovery'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    auth='Basic '+base64.b64encode(('liquiditylab:'+'p'*32).encode()).decode()
    def get(path):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:
            c.request('GET',path,headers={'Authorization':auth});r=c.getresponse();return r.status,json.loads(r.read())
        finally:c.close()
    try:
        status,body=get('/api/hosting');assert status==200 and body['seed_ready'] is False
        assert get('/healthz')[0]==200
        assert get('/api/datasets')==(503,{'error':'DATA_RESTORING_RETRY'})
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


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
        code,headers,body=request('GET','/')
        assert code==200 and 'WWW-Authenticate' not in headers and b'/dashboard' in body
        assert b'intro.css' in body and b'<style>' not in body
        code,headers,css=request('GET','/intro.css')
        assert code==200 and 'text/css' in headers['Content-Type'] and b'--paper' in css
        assert "style-src 'self'" in headers['Content-Security-Policy']
        assert password.encode() not in body and token.encode() not in body and b'/api/' not in body
        assert request('GET','/dashboard')[0]==401
        assert request('GET','/index.html')[0]==401
        assert request('GET','/api/datasets')[0]==401
        assert request('GET','/style.css')[0]==401
        assert request('GET','/dashboard',headers={'Authorization':basic})[0]==200
        assert request('GET','/',headers={'Host':'evil.example'})[0]==403
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
        assert request('POST','/api/relay',b'not-a-gzip',headers)[0]==400
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
    package=dict(dataset_id='b'*64,symbol='ACB',day='2026-11-13',frames=[packet()['records'][0]['frame']],
        event_patches=[dict(at_index=0,event=dict(id='c'*64,state='ACTIVE'))])
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


def test_runtime_secret_file_does_not_load_broker_keys_or_override_platform_values(tmp_path,monkeypatch):
    from scripts.serve_monitor_cloud import load_secret_file
    monkeypatch.delenv('MONITOR_WEB_PASSWORD',raising=False);monkeypatch.delenv('SSI_API_KEY',raising=False)
    monkeypatch.setenv('MONITOR_INGEST_TOKEN','platform-value')
    file=tmp_path/'monitor.env';file.write_text('MONITOR_WEB_PASSWORD=fixture-web-password\nMONITOR_INGEST_TOKEN=file-value\nSSI_API_KEY=must-not-load\n')
    assert 'MONITOR_WEB_PASSWORD' in load_secret_file(file)
    import os
    assert os.environ['MONITOR_WEB_PASSWORD']=='fixture-web-password' and os.environ['MONITOR_INGEST_TOKEN']=='platform-value'
    assert 'SSI_API_KEY' not in os.environ


def test_verified_unit_revision_restores_parameters_under_new_identity_without_remapping_reviews(tmp_path,monkeypatch):
    from scripts.serve_orderbook_monitor import MonitorServer
    from src.monitoring.events import parameters,EventConfig
    from src.monitoring.spread import SpreadConfig
    class Reviews:
        def __init__(self):
            self.rows=[dict(dataset_id='a'*64,day='2026-11-13',symbol='ACB',parameters=parameters(SpreadConfig(),EventConfig()))]
            self.revisions=[];self.notes=[dict(dataset_id='a'*64,note='keep original evidence')]
        def analyses(self):return list(self.rows)
        def analysis_revisions(self):return list(self.revisions)
        def record_analysis(self,e):self.rows.append(e)
        def record_analysis_revision(self,old,new):self.revisions.append(dict(old_dataset_id=old,new_dataset_id=new))
    manifest=tmp_path/'data/processed/dnse/manifest.json';manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(dict(state='UNIT_REVISION_AUDITED')))
    server=CloudServer(tmp_path,port=0,origin='http://127.0.0.1:0',read_password='p'*32,ingest_token='x'*32,bind_host='127.0.0.1')
    reviews=Reviews();server.reviews=reviews;entries=[];calls=[]
    server.index=lambda:entries
    server.base_index=lambda:entries
    def analyze(self,day,symbol,cfg,events):
        calls.append(day);entry=dict(dataset_id='b'*64,day=day,symbol=symbol,parameters=reviews.rows[0]['parameters'])
        entries.append(entry);return dict(dataset=entry)
    monkeypatch.setattr(MonitorServer,'analyze',analyze)
    try:
        server.restore_analyses();server.restore_analyses()
        assert len(calls)==1 and len(reviews.rows)==2
        assert reviews.revisions==[dict(old_dataset_id='a'*64,new_dataset_id='b'*64)]
        assert reviews.notes==[dict(dataset_id='a'*64,note='keep original evidence')]
        entries.clear();reviews.revisions=[];manifest.write_text(json.dumps(dict(state='NORMALIZED_AND_AUDITED')))
        with pytest.raises(ValueError,match='IDENTITY_CHANGED'):server.restore_analyses()
    finally:server.server_close()


def test_public_recovery_restores_verified_ciphertext_without_rest_or_credentials(tmp_path):
    import io
    source=tmp_path/'source';source.mkdir()
    name='data/exports/orderbook-monitor/index.json';data=b'{"datasets":[]}'
    (source/name).parent.mkdir(parents=True);(source/name).write_bytes(data)
    (source/'seed-manifest.json').write_text(json.dumps(dict(format='liquiditylab-private-web-seed-v1',datasets=0,
        files=[dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())])))
    tag='monitor-web-seed-20261115-fixture';encrypted=tmp_path/'encrypted';key=b'x'*32
    encrypt_seed(source,key,tag,encrypted)
    seen=[];api=PublicReleases(REPOSITORY)
    class Opener:
        def open(self,request,timeout):
            assert not request.has_header('Authorization')
            assert request.full_url.startswith('https://github.com/'+REPOSITORY+'/releases/download/'+tag+'/')
            seen.append(request.full_url)
            return io.BytesIO((encrypted/request.full_url.rsplit('/',1)[1]).read_bytes())
    api._opener=Opener()
    assert restore_seed(tmp_path/'restored',key,tag,api)['state']=='DECRYPTED_AND_VERIFIED'
    assert (tmp_path/'restored'/name).read_bytes()==data and len(seen)>=3


def test_public_release_manifest_rejects_paths_sizes_and_wrong_context(monkeypatch):
    api=PublicReleases(REPOSITORY);tag='ssi-ws-20261115-fixture';path=api.base+'/releases/tags/'+tag
    outer=dict(format='ssi-encrypted-parquet-v1',context=REPOSITORY+':'+tag,
               parts=[dict(name='part-0000.enc',bytes=100,sha256='a'*64)])
    monkeypatch.setattr(api,'download',lambda url,limit:json.dumps(outer).encode())
    assert len(api.request('GET',path)['assets'])==2
    outer['parts'][0]['name']='../escape.enc'
    with pytest.raises(ValueError,match='PART_INVALID'):api.request('GET',path)
    outer['parts'][0].update(name='part-0000.enc',bytes=100_000_000)
    with pytest.raises(ValueError,match='PART_INVALID'):api.request('GET',path)
    outer['context']='another-repository:'+tag
    with pytest.raises(ValueError,match='CONTEXT'):api.request('GET',path)
    with pytest.raises(ValueError,match='READ_ONLY'):api.request('POST',path)


def test_public_release_feed_limits_scope_and_xml_entities(monkeypatch):
    import io
    api=PublicReleases(REPOSITORY);tag='ssi-ws-20261115-fixture'
    feed=('<feed xmlns="http://www.w3.org/2005/Atom"><entry><link rel="alternate" href="'+api.public+
          '/releases/tag/'+tag+'"/></entry><entry><link href="https://evil.example/releases/tag/'+tag+'"/></entry></feed>').encode()
    monkeypatch.setattr(api,'download',lambda url,limit:feed)
    assert api.request('GET',api.base+'/releases?per_page=100')==[dict(tag_name=tag)]
    feed=b'<!DOCTYPE feed [<!ENTITY x "unsafe">]><feed/>'
    with pytest.raises(ValueError,match='FEED_INVALID'):api.request('GET',api.base+'/releases?per_page=100')
    api=PublicReleases(REPOSITORY)
    class Opener:
        def open(self,request,timeout):return io.BytesIO(b'12345')
    api._opener=Opener()
    with pytest.raises(ValueError,match='RESPONSE_LIMIT'):api.download(api.public+'/releases.atom',4)
    with pytest.raises(ValueError,match='UNAPPROVED'):api.download('https://evil.example/releases.atom',4)
