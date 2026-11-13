"""Bounded SSI V3 DATA/quote probe; no trading channels, token files or scaling."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
import ssl
import time

from src.data_providers.base import ProviderError
from src.data_providers.ssi_readonly import reject_constant, error_code

URL = 'wss://stream.ssi.com.vn/ws/v3'
MAX_FRAMES = 200
MAX_BYTES = 2 * 1024 * 1024


def subscription(symbols):
    if not 1 <= len(symbols) <= 3 or len(set(symbols)) != len(symbols) or any(
            not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9]{1,12}', s) for s in symbols):
        raise ValueError('SSI_WS_INVALID_SYMBOL_SCOPE')
    return {'method':'SUBSCRIBE', 'channel':'DATA',
            'topics':['quote.'+'-'.join(symbols)+'@tick']}


def connector(*args, **kwargs):
    from websockets.asyncio.client import connect
    class NoRedirectConnect(connect):
        def process_redirect(self, exc):
            return exc
    return NoRedirectConnect(*args, **kwargs)


def quote_observation(frame, symbols, credentials=()):
    topic = frame.get('topic')
    if frame.get('channel') != 'DATA' or not isinstance(topic, str) or not topic.lower().startswith('quote.'):
        return None
    payload = frame.get('data')
    if not isinstance(payload, dict) or payload.get('s') not in symbols:
        raise ProviderError('SSI_WS_QUOTE_SCOPE_MISMATCH')
    if not any(side in payload for side in ('bids','asks')):
        raise ProviderError('SSI_WS_QUOTE_SCHEMA_INVALID')
    for side in ('bids','asks'):
        levels = payload.get(side)
        if levels is not None and (not isinstance(levels, list) or len(levels)>100 or any(
                not isinstance(pair,list) or len(pair)!=2 or any(
                    type(v) not in (str,int,float,type(None)) for v in pair) for pair in levels)):
            raise ProviderError('SSI_WS_QUOTE_SCHEMA_INVALID')
    # Keep the market payload's original decimal strings and missing fields.
    clean = {k:payload[k] for k in ('s','t','bids','asks') if k in payload}
    encoded = json.dumps(clean, ensure_ascii=True)
    if any(secret and secret in encoded for secret in credentials):
        raise ProviderError('SSI_WS_AUTH_MATERIAL_IN_PAYLOAD')
    return {'provider':'ssi', 'api_version':'v3', 'capture_method':'websocket_bounded_probe',
            'received_at_utc':datetime.now(timezone.utc).isoformat(), 'topic':topic,
            'unit_verification_status':'UNVERIFIED_SSI_QUOTE', 'payload':clean,
            'payload_sha256':hashlib.sha256(encoded.encode()).hexdigest()}


async def run_probe(token, symbols=('ACB','FPT','HPG'), seconds=15, *, connect=None, credentials=()):
    request=subscription(symbols)
    if type(seconds) is not int or not 1<=seconds<=30:
        raise ValueError('SSI_WS_INVALID_DURATION')
    if not isinstance(token,str) or not token or any(c.isspace() for c in token):
        raise ValueError('SSI_WS_INVALID_TOKEN')
    report=dict(provider='ssi',api_version='v3',purpose='READ_ONLY_QUOTE_PREFLIGHT',
        started_at_utc=datetime.now(timezone.utc).isoformat(),symbols_requested=list(symbols),
        observe_seconds=seconds,transport_upgraded=False,subscription_acknowledged=False,
        frames_received=0,bytes_received=0,quotes_retained=0,units_verified=False,
        finance_enabled=False,stage='connect',state='STARTING')
    observations=[]
    context=ssl.create_default_context()
    if __import__('os').name=='nt':
        try:
            import truststore
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            pass
    try:
        async with asyncio.timeout(seconds+20):
            async with (connect or connector)(URL,ssl=context,proxy=None,open_timeout=10,
                    close_timeout=3,ping_interval=20,ping_timeout=10,max_size=256*1024,max_queue=8,
                    additional_headers={'Authorization':'Bearer '+token,
                                        'Content-Type':'application/json','Accept':'application/json'}) as ws:
                report.update(transport_upgraded=True,stage='subscribe')
                await ws.send(json.dumps(request))
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    try:
                        raw=await asyncio.wait_for(ws.recv(),max(.001,deadline-time.monotonic()))
                    except TimeoutError:
                        break
                    encoded=raw.encode() if isinstance(raw,str) else raw
                    report['frames_received']+=1;report['bytes_received']+=len(encoded)
                    if report['frames_received']>MAX_FRAMES or report['bytes_received']>MAX_BYTES:
                        raise ProviderError('SSI_WS_FRAME_BUDGET_EXCEEDED')
                    frame=json.loads(encoded,parse_float=str,parse_constant=reject_constant)
                    if not isinstance(frame,dict):
                        raise ProviderError('SSI_WS_FRAME_INVALID')
                    if frame.get('channel')=='TRADING':
                        raise ProviderError('SSI_WS_UNREQUESTED_PRIVATE_CHANNEL')
                    code=frame.get('code')
                    if str(code).isdigit() and int(code)>=400:
                        raise ProviderError('SSI_WS_SERVER_CODE_'+str(int(code)))
                    if frame.get('channel')=='HEARTBEAT' and frame.get('method')=='PING':
                        pong={'method':'PONG','channel':'HEARTBEAT'}
                        stamp=frame.get('time')
                        if isinstance(stamp,str) and len(stamp)<=64:pong['time']=stamp
                        await ws.send(json.dumps(pong))
                    if frame.get('method')=='SUBSCRIBE' and 'topic' not in frame:
                        status=str(frame.get('status','')).lower()
                        if status in ('error','denied','401','403','429'):
                            raise ProviderError('SSI_WS_SUBSCRIPTION_REJECTED')
                        if status in ('success','ok','200') and frame.get('channel')=='DATA':
                            report.update(subscription_acknowledged=True,stage='observe')
                    observation=quote_observation(frame,symbols,(*credentials,token))
                    if observation:
                        observations.append(observation)
                        report['quotes_retained']=len(observations)
                        report['stage']='observe'
                    if len(observations)>=100:break
        report['state']=('QUOTES_OBSERVED_UNVERIFIED' if observations else
                         'SUBSCRIBED_NO_QUOTES' if report['subscription_acknowledged'] else
                         'NO_SUBSCRIPTION_ACK')
    except Exception as error:
        from websockets.exceptions import InvalidStatus, ConnectionClosed
        if isinstance(error,InvalidStatus):
            code='SSI_WS_HTTP_'+str(error.response.status_code)
            report['http_status']=error.response.status_code
            content_type=str(error.response.headers.get('Content-Type','')).lower()
            report['http_body_format']=('JSON' if 'json' in content_type else
                                        'HTML' if 'html' in content_type else 'OTHER')
            try:
                body=json.loads(error.response.body)
                server_code=body.get('code') if isinstance(body,dict) else None
                if type(server_code) is int and 100<=server_code<=999999999:
                    report['server_error_code']=server_code
            except (ValueError,TypeError):
                pass
        elif isinstance(error,ConnectionClosed):
            code='SSI_WS_CONNECTION_CLOSED'
        elif isinstance(error,ProviderError):
            code=str(error)
        elif isinstance(error,(OSError,TimeoutError)):
            code=error_code(error)
        else:
            code='SSI_WS_'+type(error).__name__.upper()
        report.update(state='FAILED',error_code=code)
    report['samples']=[dict(symbol=s,quotes=sum(o['payload']['s']==s for o in observations),
                           bid_levels=[len(o['payload'].get('bids') or []) for o in observations if o['payload']['s']==s],
                           ask_levels=[len(o['payload'].get('asks') or []) for o in observations if o['payload']['s']==s]) for s in symbols]
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    return report, observations
