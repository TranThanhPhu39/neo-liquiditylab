"""Anonymous viewing must not expose owner notes or grant write/relay access."""
import base64
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from scripts.serve_monitor_cloud import CloudServer
from scripts.serve_orderbook_monitor import ReviewStore


class Reviews(ReviewStore):
    def analyses(self):return []
    def analysis_revisions(self):return []


def test_catalog_and_hosting_do_not_overlap_recovery_or_other_heavy_reads(public_monitor):
    server,request,did=public_monitor
    server.heavy.acquire()
    try:
        for path in ('/api/datasets','/api/hosting','/api/reviews?dataset_id='+did):
            assert request(path)[0]==503
        assert request('/healthz')[0]==200
        server.recovery=SimpleNamespace(seed_ready=False,state='RECOVERING',close=lambda:None)
        assert request('/api/hosting')[0]==200
    finally:
        server.recovery=None;server.heavy.release()
    assert request('/api/datasets')[0]==200
    assert request('/api/hosting')[0]==200


def test_public_breakout_research_is_read_only_and_uses_shared_resource_budget(public_monitor):
    server,request,did=public_monitor
    server.trades=lambda value:dict(symbol='ACB',day='2026-11-19',trades=[])
    path='/api/research/breakout?dataset_id='+did+'&cutoff=2026-11-19T07:00:00Z&reference_price=100'
    status,headers,raw=request(path)
    assert status==200 and json.loads(raw)['state']=='NO_OBSERVED_TRADES'
    assert b'PRIVATE OWNER NOTE' not in raw
    assert request('/breakout.js')[0]==200
    server.heavy.acquire()
    try:assert request(path)[0]==503
    finally:server.heavy.release()
    assert request(path.replace('reference_price=100','reference_price=NaN'))[0]==404
    assert request('/api/research/breakout',method='POST',body='{}')[0]==401


@pytest.fixture
def public_monitor(tmp_path):
    password='fixture-owner-password-32-characters'
    server=CloudServer(tmp_path,port=0,origin='http://127.0.0.1:0',read_password=password,
                       ingest_token='fixture-relay-token-32-characters',bind_host='127.0.0.1',public_read=True)
    server.public_origin='http://127.0.0.1:'+str(server.server_port)
    did='a'*64;eid='b'*64
    event=dict(id=eid,type='SPREAD_WIDENING',detected_at='2026-11-19T02:15:00Z',state='ENDED')
    pack=dict(dataset_id=did,symbol='ACB',day='2026-11-19',frames=[],event_patches=[])
    path=tmp_path/'fixture.json';path.write_text(json.dumps(pack))
    server.index=lambda:[dict(dataset_id=did,symbol='ACB',day='2026-11-19',observations=1)]
    server.dataset=lambda value:path if value==did else (_ for _ in ()).throw(ValueError('UNKNOWN_DATASET'))
    server.summary=lambda value:dict(dataset_id=did,symbol='ACB',day='2026-11-19',events=[event],quality={})
    server.trades=lambda value:dict(trades=[])
    server.reviews=Reviews(tmp_path/'reviews.db')
    for dataset_id in (did,server.live.dataset_id('c'*32,'ACB')):
        server.reviews.append(dict(dataset_id=dataset_id,event_id=eid,verdict='uncertain',note='PRIVATE OWNER NOTE'))
    server.live.events=lambda *args:[event]
    basic='Basic '+base64.b64encode(('liquiditylab:'+password).encode()).decode()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()

    def request(path,*,owner=False,method='GET',body=None,headers=None):
        sent=dict(headers or {})
        if owner:sent['Authorization']=basic
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            conn.request(method,path,body,sent);response=conn.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally:conn.close()

    try:yield server,request,did
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_public_pages_and_read_apis_do_not_challenge_or_reveal_csrf(public_monitor):
    server,get,did=public_monitor
    for path in ('/','/dashboard','/index.html','/app.js','/capture-model.js','/live-model.js',
                 '/model.js','/style.css','/api/hosting','/api/datasets','/api/live/status','/api/live/runs',
                 '/api/trades?dataset_id='+did):
        code,headers,body=get(path)
        assert code==200 and 'WWW-Authenticate' not in headers,path
        assert server.read_password.encode() not in body and server.ingest_token.encode() not in body
    for path in ('/api/datasets','/api/live/status'):
        public=json.loads(get(path)[2]);owner=json.loads(get(path,owner=True)[2])
        assert public['read_only'] is True and public['csrf'] is None
        assert owner['read_only'] is False and owner['csrf']==server.csrf
    for path in ('/.env','/favicon.ico','/api/unknown'):
        code,headers,_=get(path)
        assert code==404 and 'WWW-Authenticate' not in headers
    for path in ('/realtime','/realtime/'):
        code,headers,_=get(path)
        assert code==303 and headers['Location']=='/dashboard?view=capture'
        assert 'WWW-Authenticate' not in headers
    html=get('/dashboard')[2]
    assert b'id="dataMode"' in html and b'id="captureRun"' in html
    assert b'href="/realtime"' not in html and b'<iframe' not in html
    assert get('/dashboard',headers={'Host':'evil.example'})[0]==403


def test_private_notes_are_redacted_from_every_public_read_and_export(public_monitor):
    _,get,did=public_monitor
    paths=('/api/reviews?dataset_id='+did,'/api/library','/api/export?dataset_id='+did,
           '/api/export?dataset_id='+did+'&format=csv',
           '/api/live/reviews?run_id='+'c'*32+'&symbol=ACB',
           '/api/live/export?run_id='+'c'*32+'&symbol=ACB')
    for path in paths:
        code,headers,body=get(path)
        assert code==200 and 'WWW-Authenticate' not in headers,path
        assert b'PRIVATE OWNER NOTE' not in body,path
        assert b'PRIVATE OWNER NOTE' in get(path,owner=True)[2],path
    assert json.loads(get('/api/reviews?dataset_id='+did)[2])==[]
    assert json.loads(get('/api/export?dataset_id='+did)[2])['reviews']==[]
    assert json.loads(get('/api/library')[2])[0]['review'] is None
    assert get('/api/review-history')[0]==401
    assert b'PRIVATE OWNER NOTE' in get('/api/review-history',owner=True)[2]


def test_public_cannot_write_even_with_csrf_and_owner_still_can(public_monitor):
    server,request,did=public_monitor
    headers={'Content-Type':'application/json','Origin':server.public_origin,'X-Monitor-CSRF':server.csrf}
    for path in ('/api/reviews','/api/live/reviews','/api/analyze','/api/live/start','/api/live/stop','/api/relay'):
        assert request(path,method='POST',body='{}',headers=headers)[0]==401,path
    assert request('/admin')[0]==401
    code,redirect,_=request('/admin',owner=True)
    assert code==303 and redirect['Location']=='/dashboard'
    note=json.dumps(dict(dataset_id=did,event_id='b'*64,verdict='useful',note='SECOND PRIVATE NOTE'))
    assert request('/api/reviews',owner=True,method='POST',body=note,headers=headers)[0]==200
    assert b'SECOND PRIVATE NOTE' in request('/api/reviews?dataset_id='+did,owner=True)[2]
    assert b'SECOND PRIVATE NOTE' not in request('/api/reviews?dataset_id='+did)[2]
    assert request('/api/reviews',owner=True,method='POST',body=note)[0]==403


def test_public_cold_start_returns_restoring_instead_of_a_login_prompt(public_monitor):
    server,get,_=public_monitor
    server.recovery=SimpleNamespace(seed_ready=False,state='RECOVERING',close=lambda:None)
    server.index=lambda:(_ for _ in ()).throw(AssertionError('Catalog accessed during recovery'))
    assert get('/dashboard')[0]==200
    code,headers,body=get('/api/datasets')
    assert code==503 and json.loads(body)['error']=='DATA_RESTORING_RETRY'
    assert 'WWW-Authenticate' not in headers
    assert json.loads(get('/api/hosting')[2])['seed_ready'] is False


def test_heavy_budget_exhaustion_does_not_block_health_or_request_a_login(public_monitor):
    server,get,_=public_monitor
    with server.heavy:
        for path in ('/api/library','/api/live/view?run_id='+'c'*32+'&symbol=ACB'):
            code,headers,body=get(path)
            assert code==503 and json.loads(body)['error']=='SERVER_BUSY_RETRY'
            assert headers['Retry-After']=='3'
            assert 'WWW-Authenticate' not in headers
        assert get('/healthz')[0]==200
        assert get('/dashboard')[0]==200
        code,_,body=get('/api/relay',method='POST',headers={'Authorization':'Bearer '+server.ingest_token})
        assert code==503 and json.loads(body)['error']=='SERVER_BUSY_RETRY'
        assert server.live.journal is None
    assert get('/api/library')[0]==200


def test_hosted_export_reads_metadata_without_materializing_replay_frames(public_monitor):
    from scripts.serve_orderbook_monitor import report_payload
    server,_,did=public_monitor
    server.metadata=lambda value:dict(dataset_id=did,provider='dnse',price_unit='VND')
    server.dataset=lambda value:(_ for _ in ()).throw(AssertionError('Full replay loaded for event export'))
    result=report_payload(server,did,include_reviews=False)
    assert result['provenance']['price_unit']=='VND' and result['reviews']==[]


def test_background_recovery_busy_response_identifies_recovery_after_seed_is_ready(public_monitor):
    server,get,_=public_monitor
    server.recovery=SimpleNamespace(seed_ready=True,state='RECOVERING',close=lambda:None)
    with server.heavy:
        code,headers,body=get('/api/library')
        assert code==503 and json.loads(body)['error']=='DATA_RESTORING_RETRY'
        assert headers['Retry-After']=='3'
        assert get('/healthz')[0]==200


def test_catalog_response_keeps_quality_labels_without_full_storage_provenance(public_monitor):
    server,get,did=public_monitor
    entry=dict(dataset_id=did,symbol='VNM',day='2026-11-19',observations=3,market_observation=True,
        compact_folder='data/private-large-folder',source_archive='not-needed-for-navigation',
        quality=dict(unit_counts={'ASSUMED_SHARED_SCHEMA':3},raw_catalog=['large-source-metadata']))
    server.index=lambda:[entry]
    result=json.loads(get('/api/datasets')[2])['datasets'][0]
    assert result['dataset_id']==did and result['observations']==3
    assert result['quality']['unit_counts']=={'ASSUMED_SHARED_SCHEMA':3}
    assert 'compact_folder' not in result and 'source_archive' not in result
    assert 'raw_catalog' not in result['quality']
    assert server.index()[0]==entry
    import gzip
    code,headers,compressed=get('/api/datasets',headers={'Accept-Encoding':'gzip'})
    assert code==200 and headers['Content-Encoding']=='gzip'
    assert json.loads(gzip.decompress(compressed))['datasets']==[result]
