"""Read-only SSI DATA stream. Credentials and control/private payloads never persist."""
import asyncio
from datetime import datetime, timezone
import json
import re
import ssl
import time

from src.data_providers.base import ProviderError
from src.data_providers.ssi_readonly import reject_constant
from src.data_providers.ssi_ws_probe import URL, connector

SYMBOLS=('ACB','FPT','HPG')
FIELDS={'quote':('s','t','bids','asks'), 'trade':('s','t','p','q','si','v'),
        'market':('s','b','t','f','ce','fl','ref')}


def requests(symbols):
    if not symbols or len(set(symbols))!=len(symbols) or any(s not in SYMBOLS for s in symbols):
        raise ValueError('SSI_LIVE_INVALID_SYMBOLS')
    return [dict(method='SUBSCRIBE',channel='DATA',topics=[kind+'.'+symbol+('@tick' if kind!='market' else '')])
            for symbol in symbols for kind in FIELDS]+[dict(method='SUBSCRIBE',channel='DATA',topics=['indexsummary.VNINDEX']),
                                                        dict(method='SUBSCRIBE',channel='DATA',topics=['market.hose'])]


def public_record(frame,symbols,credentials=(),received_at=None):
    if frame.get('channel')=='TRADING':raise ProviderError('SSI_UNREQUESTED_PRIVATE_CHANNEL')
    if frame.get('channel')!='DATA' or not frame.get('topic'):return None
    topic=frame['topic']
    if not isinstance(topic,str):raise ProviderError('SSI_INVALID_TOPIC')
    kind=topic.split('.')[0]
    if kind not in FIELDS and kind!='indexsummary':raise ProviderError('SSI_UNREQUESTED_TOPIC')
    data=frame.get('data')
    if not isinstance(data,dict):raise ProviderError('SSI_INVALID_DATA_SCHEMA')
    if kind=='indexsummary':
        if topic!='indexsummary.VNINDEX' or data.get('s') not in ('VNINDEX','VNIND') or data.get('b')!='HOSE':
            raise ProviderError('SSI_TOPIC_SCOPE_MISMATCH')
        clean={k:data[k] for k in ('s','b','t','ss') if k in data}
        if any(not isinstance(v,str) or len(v)>64 for v in clean.values()):raise ProviderError('SSI_INVALID_SESSION_SCHEMA')
        encoded=json.dumps(clean)
        if any(secret and secret in encoded for secret in credentials):raise ProviderError('SSI_AUTH_MATERIAL_IN_DATA')
        return dict(kind='MARKET',symbol='',topic=topic,payload=dict(b=clean.get('b'),t=clean.get('t'),f=clean.get('ss'),index_symbol=clean.get('s')),
            received_at=received_at or datetime.now(timezone.utc).isoformat())
    symbol=data.get('s')
    if kind=='market' and topic.lower()=='market.hose' and symbol is not None and symbol not in symbols:
        return None  # subscribe to the board flag, not other securities' reference data
    # Session flags are board-wide. Never keep other securities or private fields.
    flag=kind=='market' and symbol is None and data.get('b')=='HOSE' and isinstance(data.get('f'),str)
    if not flag and symbol not in symbols:raise ProviderError('SSI_SYMBOL_SCOPE_MISMATCH')
    topic_symbol=topic.split('.',1)[-1].split('@')[0]
    if topic_symbol not in symbols and not (flag and topic_symbol.upper()=='HOSE'):
        raise ProviderError('SSI_TOPIC_SCOPE_MISMATCH')
    clean={k:data[k] for k in FIELDS[kind] if k in data}
    if kind=='quote':
        for side in ('bids','asks'):
            levels=clean.get(side)
            if levels is not None and (not isinstance(levels,list) or len(levels)>10 or any(
                not isinstance(p,list) or len(p)!=2 or any(type(x) not in (str,int,float,type(None)) for x in p) for p in levels)):
                raise ProviderError('SSI_QUOTE_SCHEMA_INVALID')
    elif any(type(v) not in (str,int,float,type(None)) for v in clean.values()):
        raise ProviderError('SSI_DATA_SCHEMA_INVALID')
    encoded=json.dumps(clean,ensure_ascii=True)
    if any(secret and secret in encoded for secret in credentials):raise ProviderError('SSI_AUTH_MATERIAL_IN_DATA')
    return dict(kind=kind.upper(),symbol=symbol or '',topic=topic,payload=clean,
                received_at=received_at or datetime.now(timezone.utc).isoformat())


async def collect(client,symbols,seconds,stop,on_record,on_state,*,connect=None,backoff=1):
    requests(symbols)
    deadline=time.monotonic()+seconds;attempt=0
    context=ssl.create_default_context()
    if __import__('os').name=='nt':
        try:
            import truststore
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:pass
    while not stop.is_set() and time.monotonic()<deadline:
        attempt+=1
        on_state('CONNECTING',dict(connection=attempt))
        try:
            await asyncio.to_thread(client.authenticate)
            async with (connect or connector)(URL,ssl=context,proxy=None,open_timeout=10,close_timeout=3,
                    ping_interval=20,ping_timeout=15,max_size=262144,max_queue=32,
                    additional_headers={'Authorization':'Bearer '+client._token,'Content-Type':'application/json','Accept':'application/json'}) as ws:
                for request in requests(symbols):await ws.send(json.dumps(request))
                on_state('CONNECTED_WAITING_DATA',dict(connection=attempt))
                while not stop.is_set() and time.monotonic()<deadline:
                    if client._expires_at<=time.time()+45:
                        on_state('RECONNECTING',dict(reason='TOKEN_REFRESH',connection=attempt));break
                    try:raw=await asyncio.wait_for(ws.recv(),min(1,max(.001,deadline-time.monotonic())))
                    except TimeoutError:
                        on_state('TICK',{});continue
                    if len(raw)>262144:raise ProviderError('SSI_FRAME_TOO_LARGE')
                    frame=json.loads(raw,parse_float=str,parse_constant=reject_constant)
                    if not isinstance(frame,dict):raise ProviderError('SSI_FRAME_INVALID')
                    if frame.get('channel')=='TRADING':raise ProviderError('SSI_UNREQUESTED_PRIVATE_CHANNEL')
                    code=frame.get('code')
                    if str(code).isdigit() and int(code)>=400:raise ProviderError('SSI_SERVER_'+str(int(code)))
                    if frame.get('channel')=='HEARTBEAT' and frame.get('method')=='PING':
                        pong=dict(method='PONG',channel='HEARTBEAT')
                        if isinstance(frame.get('time'),str) and len(frame['time'])<=64:pong['time']=frame['time']
                        await ws.send(json.dumps(pong));continue
                    if frame.get('method')=='SUBSCRIBE':
                        if str(frame.get('status','')).lower() in ('error','denied','401','403','429'):
                            raise ProviderError('SSI_SUBSCRIPTION_REJECTED')
                        on_state('SUBSCRIPTION_ACK',dict(status=str(frame.get('status','UNKNOWN'))[:16]));continue
                    record=public_record(frame,symbols,(client._key,client._secret,client._token))
                    if record:
                        record['connection']=attempt
                        on_record(record)
        except Exception as error:
            code=str(error) if isinstance(error,ProviderError) and re.fullmatch('[A-Z0-9_]{1,80}',str(error)) else 'SSI_'+type(error).__name__.upper()
            on_state('DISCONNECTED',dict(reason=code,connection=attempt))
            if code in ('SSI_UNREQUESTED_PRIVATE_CHANNEL','SSI_AUTH_MATERIAL_IN_DATA','SSI_SYMBOL_SCOPE_MISMATCH','SSI_TOPIC_SCOPE_MISMATCH'):
                on_state('FAILED',dict(reason=code));return
            for _ in range(min(30,backoff*attempt)):
                if stop.is_set() or time.monotonic()>=deadline:break
                await asyncio.sleep(1)
    on_state('STOPPED',dict(reason='USER_STOP' if stop.is_set() else 'DURATION_LIMIT'))
