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
