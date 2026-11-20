import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import time
import shutil
import subprocess
from pathlib import Path

import pytest
pytest.importorskip('pyarrow')

from src.data_providers.ssi_stream import requests,public_record,collect
from src.data_pipeline.ssi_live_store import LiveJournal,read_records
from src.data_pipeline.ssi_live_normalization import load_contract,normalize_quote,normalize_trade
from src.monitoring.ssi_live import SSILive
from src.monitoring.events import OfflineEngine
from src.monitoring.spread import SpreadConfig


def observation(kind='QUOTE',second=0,symbol='ACB',**changes):
    received=datetime(2026,11,13,3,10,tzinfo=timezone.utc)+timedelta(seconds=second)
    payload=dict(s=symbol,t=received.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y/%m/%d %H:%M:%S'))
    if kind=='QUOTE':payload.update(bids=[[str(20000-i*50),str(1000+i)] for i in range(10)],asks=[[str(20050+i*50),str(2000+i)] for i in range(10)])
    elif kind=='TRADE':payload.update(p='20050',q='100',si='B',v='1000')
    else:payload.update(b='HOSE',f='LO');payload.pop('s')
    payload.update(changes)
    return dict(kind=kind,symbol=symbol if kind!='MARKET' else '',payload=payload,received_at=received.isoformat(),connection=1,topic=kind.lower()+'.ACB@tick')


def manual_service(root):
    s=SSILive(root);s.journal=LiveJournal(root,['ACB'])
    s.engines={'ACB':OfflineEngine('fixture')}
    return s


def test_data_subscriptions_are_individual_and_private_messages_or_scope_escape_never_persist():
    rows=requests(['ACB','FPT'])
    assert len(rows)==9 and all(r['channel']=='DATA' and len(r['topics'])==1 for r in rows)
    assert any(r['topics']==['market.flag'] for r in rows)
    assert rows[0]['topics']==['quote.ACB@tick']
    for symbols in [[],['ACB','ACB'],['OTHER'],['ACB;order.*']]:
        with pytest.raises(ValueError):requests(symbols)
    frame=dict(channel='DATA',topic='quote.ACB@tick',data=observation()['payload'])
    clean=public_record(frame,['ACB'])
    assert clean['payload']['bids'][0]==['20000','1000']
    frame['data']['bids'][0][1]='fixture-live-secret'
    with pytest.raises(Exception,match='AUTH_MATERIAL'):public_record(frame,['ACB'],('fixture-live-secret',))
    with pytest.raises(Exception,match='PRIVATE'):public_record(dict(channel='TRADING',data={'account':'secret'}),['ACB'])
    with pytest.raises(Exception,match='SCOPE'):public_record(dict(channel='DATA',topic='quote.FPT@tick',data=observation()['payload']),['ACB'])


def test_ssi_units_are_never_dnse_scaled_and_partial_books_do_not_forward_fill(tmp_path):
    c=load_contract(tmp_path);o=observation()
    r,levels=normalize_quote(o,c,'id')
    assert r['bid_price_1']=='20000' and r['bid_vol_1']==1000
    assert r['unit_verification_status']=='UNVERIFIED_SSI_QUOTE' and len(levels['bids'])==10
    engine=OfflineEngine('scope');f=engine.push(r,0)
    assert not f['eligible'] and f['reason']=='UNVERIFIED_UNITS'
    partial=deepcopy(o);partial['payload'].pop('asks')
    r,levels=normalize_quote(partial,c,'partial')
    assert levels['asks']==[] and r['ask_vol_1'] is None and r['data_quality_status']=='INVALID'
    zero=observation();zero['payload']['bids'][0][1]='0'
    assert normalize_quote(zero,c,'zero')[0]['bid_vol_1']==0
    future=observation();future['received_at']='2026-11-13T02:00:00+00:00'
    assert 'SOURCE_CLOCK_SKEW_OR_STALE' in normalize_quote(future,c,'skew')[0]['issues']


def test_only_verified_contract_and_provider_lo_session_can_make_detector_eligible(tmp_path):
    from src.monitoring.spread import features
    c=dict(load_contract(tmp_path),status='EMPIRICALLY_VERIFIED',symbols=['ACB'],quote_semantics='FULL_SNAPSHOT')
    o=observation();flag=dict(f='LO',day='2026-11-13',connection=1)
    r,_=normalize_quote(o,c,'one',flag)
    assert r['provider']=='ssi' and features(r)['eligible']
    r,_=normalize_quote(o,c,'two',dict(flag,connection=2))
    assert not features(r)['eligible'] and r['market_session']=='SSI_SESSION_UNKNOWN'
    r,_=normalize_quote(o,c,'closed-unknown',dict(flag,f='C'))
    assert not features(r)['eligible']  # never guess C means continuous
    trade=normalize_trade(observation('TRADE'),c,'trade')
    assert trade['unit_status']=='UNVERIFIED_SSI_TRADE'  # quote evidence is not trade evidence


def test_journal_recovers_unflushed_records_and_preserves_equal_timestamp_trades(tmp_path):
    j=LiveJournal(tmp_path,['ACB'],batch_size=100)
    a=observation('TRADE');a['trade']=normalize_trade(a,load_contract(tmp_path),'a'*64)
    b=deepcopy(a);b['trade']['id']='b'*64
    j.append(a);j.append(b);folder=j.folder
    j.handle.close()  # simulate interruption before a Parquet flush
    recovered=LiveJournal(tmp_path,['ACB'],folder=folder);recovered.close('RECOVERED_WITH_GAP')
    rows=read_records(folder,'ACB','TRADE')
    assert len(rows)==2 and rows[0]['trade']['id']!=rows[1]['trade']['id']
    assert json.loads((folder/'manifest.json').read_bytes())['trade_counts']['ACB']==2
    assert not list((tmp_path/'data').rglob('*.db'))


def test_live_window_delta_archive_and_trade_capture_remain_independent(tmp_path):
    s=manual_service(tmp_path)
    s.receive(observation());s.receive(observation('TRADE'));s.receive(observation('TRADE'))
    first=s.view(s.journal.run_id,'ACB')
    assert len(first['frames'])==1 and len(first['trades'])==2
    assert first['trades'][0]['id']!=first['trades'][1]['id']
    s.receive(observation(second=1))
    delta=s.view(s.journal.run_id,'ACB',after=0)
    assert not delta['reset'] and [f['index'] for f in delta['frames']]==[1]
    s.journal.close()
    archived=SSILive(tmp_path).view(s.journal.run_id,'ACB',start=0)
    assert len(archived['frames'])==2 and len(archived['trades'])==2
    assert archived['frames'][0]['units']=='UNVERIFIED_SSI_QUOTE'


def test_event_versions_are_causal_on_receipt_time_including_disconnect():
    patches=[dict(received_at='2026-11-13T03:00:00+00:00',event=dict(id='event',state='ACTIVE')),
             dict(received_at='2026-11-13T03:00:01+00:00',event=dict(id='event',state='INTERRUPTED'))]
    assert SSILive._events(patches,'2026-11-13T03:00:00.500000+00:00')[0]['state']=='ACTIVE'
    assert SSILive._events(patches,'2026-11-13T03:00:01+00:00')[0]['state']=='INTERRUPTED'


def test_collector_heartbeat_tokens_and_channels(tmp_path):
    class Client:
        _key='fixture-key';_secret='fixture-secret';_token='fixture-token';_expires_at=time.time()+3600
        def authenticate(self):return {}
    stop=threading.Event();sent=[];frames=[dict(channel='HEARTBEAT',method='PING',time='public-clock'),
        dict(channel='DATA',topic='quote.ACB@tick',data=observation()['payload']),dict(channel='TRADING',data={'account':'private'})]
    class Socket:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,x):sent.append(json.loads(x))
        async def recv(self):return json.dumps(frames.pop(0))
    calls=[];records=[];states=[]
    def connect(url,**kwargs):calls.append((url,kwargs));return Socket()
    asyncio.run(collect(Client(),['ACB'],60,stop,records.append,lambda state,info:states.append((state,info)),connect=connect))
    assert len(records)==1 and states[-1][0]=='FAILED'
    assert sent[-1]==dict(method='PONG',channel='HEARTBEAT',time='public-clock')
    assert '?' not in calls[0][0] and calls[0][1]['additional_headers']['Authorization']=='Bearer fixture-token'
    assert 'fixture-token' not in json.dumps(records)+json.dumps(states) and 'private' not in json.dumps(records)


def test_live_endpoints_keep_csrf_and_do_not_require_offline_dataset(tmp_path):
    import http.client
    from scripts.serve_orderbook_monitor import MonitorServer
    server=MonitorServer(tmp_path,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def request(method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port)
        c.request(method,path,json.dumps(body) if body is not None else None,headers or {})
        response=c.getresponse();result=response.status,response.read();c.close();return result
    try:
        code,raw=request('GET','/api/live/status');assert code==200
        csrf=json.loads(raw)['csrf'];assert json.loads(raw)['state']=='IDLE'
        assert request('POST','/api/live/start',{'symbols':['ACB'],'seconds':60})[0]==403
        headers={'Content-Type':'application/json','X-Monitor-CSRF':csrf,'Origin':base}
        assert request('POST','/api/live/start',{'symbols':['ACB;order.*'],'seconds':60},headers)[0]==400
        assert request('GET','/api/live/view?run_id=../../.env&symbol=ACB')[0]==404
        assert request('GET','/api/live/status',headers={'Host':'evil.invalid'})[0]==403
        assert not list((tmp_path/'data/realtime').glob('**/manifest.json'))
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_incomplete_journal_tail_is_preserved_and_cannot_poison_new_appends(tmp_path):
    j=LiveJournal(tmp_path,['ACB']);folder=j.folder;j.handle.close()
    (folder/'pending.jsonl').write_bytes(b'{"incomplete":')
    recovered=LiveJournal(tmp_path,['ACB'],folder=folder)
    recovered.append(dict(kind='SYSTEM',symbol='',received_at='2026-11-13T03:00:00+00:00',state='RESTART'))
    recovered.close('RECOVERED_WITH_GAP')
    assert (folder/'recovery-tail.bin').read_bytes()==b'{"incomplete":'
    manifest=json.loads((folder/'manifest.json').read_bytes())
    assert manifest['recovery_gap']=='INCOMPLETE_JOURNAL_TAIL' and manifest['last_ordinal']==0


def test_reconnect_subscribes_again_and_records_gap_without_logging_exception_text():
    class Client:
        _key='key';_secret='secret';_token='token';_expires_at=time.time()+3600
        def authenticate(self):return {}
    stop=threading.Event();calls=[];records=[];states=[]
    class Socket:
        def __init__(self,n):self.n=n;self.read=0;self.sent=[]
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,p):self.sent.append(json.loads(p))
        async def recv(self):
            self.read+=1
            if self.read==1:return json.dumps(dict(channel='DATA',topic='quote.ACB@tick',data=observation()['payload']))
            if self.n==1:raise OSError('private token value must not be logged')
            stop.set();raise TimeoutError
    def connect(*a,**k):
        s=Socket(len(calls)+1);calls.append(s);return s
    asyncio.run(collect(Client(),['ACB'],10,stop,records.append,lambda state,info:states.append((state,info)),connect=connect))
    assert len(calls)==2 and all(len(c.sent)==6 for c in calls)
    assert [r['connection'] for r in records]==[1,2]
    assert any(state=='DISCONNECTED' for state,_ in states)
    assert 'private token value' not in json.dumps(states)


@pytest.mark.skipif(not shutil.which('node'),reason='Node required for UI unit checks')
def test_realtime_page_boot_and_replay_model():
    root=Path(__file__).resolve().parents[1]
    r=subprocess.run([shutil.which('node'),'--test','tests/js/ssi-live.test.cjs','tests/js/ssi-live-ui.test.cjs'],cwd=root,
                     capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert r.returncode==0,r.stdout+r.stderr


def test_index_summary_provides_only_scoped_public_hose_session_flag():
    frame=dict(channel='DATA',topic='indexsummary.VNINDEX',data=dict(s='VNIND',b='HOSE',ss='LO',t='2026/11/13 10:10:00',v='index-value-not-needed'))
    record=public_record(frame,['ACB'])
    assert record['kind']=='MARKET' and record['payload']['f']=='LO' and 'v' not in record['payload']
    with pytest.raises(Exception,match='SCOPE'):public_record(dict(frame,topic='indexsummary.OTHER'),['ACB'])


def test_silence_resets_detector_baseline_without_inventing_quotes(tmp_path):
    s=manual_service(tmp_path);s.receive(observation());count=s.counts['ACB']
    s._check_stale(datetime.fromisoformat(observation()['received_at'])+timedelta(seconds=31))
    assert 'ACB' in s.stale_symbols and s.counts['ACB']==count
    assert s.engines['ACB'].spread.previous is None
    s.journal.close()


def test_ten_slots_with_zero_padding_are_three_observed_levels_not_ten(tmp_path):
    from src.monitoring.spread import features
    o=observation()
    for side in ('bids','asks'):o['payload'][side]=o['payload'][side][:3]+[['0','0'] for _ in range(7)]
    c=dict(load_contract(tmp_path),status='EMPIRICALLY_VERIFIED',symbols=['ACB'],quote_semantics='FULL_SNAPSHOT')
    r,levels=normalize_quote(o,c,'padding',dict(f='LO',day='2026-11-13',connection=1))
    assert r['provided_slots']==dict(bids=10,asks=10) and r['observed_levels']==dict(bids=3,asks=3)
    assert r['data_quality_status']=='VALID' and features(r)['eligible']
    assert levels['bids'][3]==[None,0] and r['bid_vol_1']==1000


def test_backup_selects_closed_ssi_partitions_and_contract_but_not_active_capture(tmp_path):
    from scripts.create_recovery_backup import candidates
    from src.data_pipeline.ssi_live_store import atomic
    j=LiveJournal(tmp_path,['ACB'])
    j.append(dict(kind='SYSTEM',symbol='',received_at='2026-11-13T03:00:00+00:00',state='START'))
    j.flush()
    assert not any(rel.as_posix().startswith('data/realtime/ssi/') for _,rel in candidates(tmp_path))
    j.close();contract=tmp_path/'data/control/ssi_live_contract.json';contract.parent.mkdir(parents=True,exist_ok=True);atomic(contract,{'status':'UNVERIFIED'})
    selected={rel.as_posix() for _,rel in candidates(tmp_path)}
    assert contract.relative_to(tmp_path).as_posix() in selected
    assert (j.folder/'part-00000000.parquet').relative_to(tmp_path).as_posix() in selected


def test_verified_ssi_episode_replay_reviews_and_export_keep_causal_event_version(tmp_path,monkeypatch):
    from decimal import Decimal
    import http.client
    from scripts import serve_orderbook_monitor as app
    s=manual_service(tmp_path)
    s.contract.update(status='EMPIRICALLY_VERIFIED',symbols=['ACB'],quote_semantics='FULL_SNAPSHOT')
    cfg=SpreadConfig(baseline_seconds=60,min_samples=3,min_span_seconds=2,min_coverage=Decimal(0),
        multiplier=Decimal(2),min_increase_bps=Decimal(1),min_duration_seconds=2,min_observations=3,
        max_observation_gap_seconds=5,end_duration_seconds=1,cooldown_seconds=2)
    s.engines={'ACB':OfflineEngine('ssi-causal-fixture',cfg)}
    s.flags['HOSE']=dict(f='LO',day='2026-11-13',connection=1)
    s.journal.manifest['unit_contract']=s.contract
    for i in range(9):
        row=observation(second=i)
        if 4<=i<=6:row['payload']['asks']=[[str(20150+n*50),str(2000+n)] for n in range(10)]
        s.receive(row)
    s.journal.close()
    at=lambda i:observation(second=i)['received_at']
    assert s.events(s.journal.run_id,'ACB',at(5))==[]
    active=s.events(s.journal.run_id,'ACB',at(6))
    event=next(e for e in active if e['type']=='SPREAD_WIDENING')
    assert event['state']=='ACTIVE'
    assert next(e for e in s.events(s.journal.run_id,'ACB',at(8)) if e['id']==event['id'])['state']=='ENDED'
    archived=SSILive(tmp_path)
    assert next(e for e in archived.events(s.journal.run_id,'ACB',at(6)) if e['id']==event['id'])['state']=='ACTIVE'
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,11,13,3,10,10,tzinfo=timezone.utc)
    monkeypatch.setattr(app,'datetime',Clock)
    server=app.MonitorServer(tmp_path,port=0);server.live=s
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    try:
        body=dict(run_id=s.journal.run_id,symbol='ACB',event_id=event['id'],verdict='useful',note='causal test')
        c=http.client.HTTPConnection('127.0.0.1',server.server_port)
        c.request('POST','/api/live/reviews',json.dumps(body),{'Content-Type':'application/json','Origin':origin,'X-Monitor-CSRF':server.csrf})
        response=c.getresponse();assert response.status==200;response.read();c.close()
        c=http.client.HTTPConnection('127.0.0.1',server.server_port)
        c.request('GET','/api/live/export?run_id='+s.journal.run_id+'&symbol=ACB&at='+at(6).replace('+','%2B'))
        response=c.getresponse();export=json.loads(response.read());c.close()
        assert next(e for e in export['events'] if e['id']==event['id'])['state']=='ACTIVE'
        assert export['reviews'][0]['note']=='causal test'
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
