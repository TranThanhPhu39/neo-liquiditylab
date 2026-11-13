"""SSI has its own units/clock contract. Never reuse DNSE multipliers."""
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo

LOCAL=ZoneInfo('Asia/Ho_Chi_Minh')


def serial(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serial(v) for v in value]
    return value


def load_contract(root):
    path=root/'data/control/ssi_live_contract.json'
    default=dict(status='UNVERIFIED',symbols=[],price_multiplier='1',volume_multiplier='1',quote_semantics='UNKNOWN')
    if not path.exists():return dict(default,hash=hashlib.sha256(json.dumps(default,sort_keys=True).encode()).hexdigest())
    c=json.loads(path.read_bytes())
    if (c.get('status')!='EMPIRICALLY_VERIFIED' or c.get('quote_semantics')!='FULL_SNAPSHOT'
            or not c.get('evidence') or not c.get('symbols') or any(s not in ('ACB','FPT','HPG') for s in c['symbols'])):
        raise ValueError('SSI_INVALID_VERIFIED_UNIT_CONTRACT')
    for key in ('price_multiplier','volume_multiplier'):
        n=Decimal(c[key])
        if not n.is_finite() or n<=0:raise ValueError('SSI_INVALID_UNIT_MULTIPLIER')
    if c.get('trade_status')=='EMPIRICALLY_VERIFIED':
        if not c.get('trade_symbols') or any(s not in c['symbols'] for s in c['trade_symbols']):raise ValueError('SSI_INVALID_TRADE_SCOPE')
        for key in ('trade_price_multiplier','trade_volume_multiplier'):
            n=Decimal(c[key])
            if not n.is_finite() or n<=0:raise ValueError('SSI_INVALID_TRADE_MULTIPLIER')
    for e in c['evidence']:
        p=(root/e['path']).resolve()
        if not p.is_relative_to(root/'data/raw/ssi') or hashlib.sha256(p.read_bytes()).hexdigest()!=e['sha256']:
            raise ValueError('SSI_UNIT_EVIDENCE_INVALID')
    return dict(c,hash=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())


def source_time(text):
    if not isinstance(text,str):raise ValueError('SSI_SOURCE_TIME_INVALID')
    for fmt in ('%Y/%m/%d %H:%M:%S','%Y/%m/%d %H:%M:%S.%f'):
        try:return datetime.strptime(text,fmt).replace(tzinfo=LOCAL).astimezone(timezone.utc)
        except ValueError:pass
    raise ValueError('SSI_SOURCE_TIME_INVALID')


def number(value,factor='1',quantity=False):
    if value is None or isinstance(value,bool):return None
    try:
        n=Decimal(str(value))*Decimal(factor)
        if not n.is_finite() or n<0 or not quantity and n==0:return None
        if quantity:return int(n) if n==n.to_integral_value() and n<=9007199254740991 else None
        return str(n)
    except (ValueError,TypeError,InvalidOperation):return None


def normalize_quote(observation,contract,identity,flag=None):
    p=observation['payload'];received=datetime.fromisoformat(observation['received_at'])
    source=source_time(p.get('t'));local=source.astimezone(LOCAL)
    verified=contract['status']=='EMPIRICALLY_VERIFIED' and p['s'] in contract['symbols']
    pm=contract['price_multiplier'] if verified else '1';vm=contract['volume_multiplier'] if verified else '1'
    levels={s:[[number(pair[0],pm),number(pair[1],vm,True)] for pair in p.get(s) or []] for s in ('bids','asks')}
    # SSI sends ten slots even when only top three are supplied. [0,0] padding
    # is not an observed price level, nor proof that outside depth is zero.
    known={s:[p for p in values if p[0] is not None and p[1] is not None] for s,values in levels.items()}
    complete=all(len(levels[s])==10 and all(a is not None and b is not None for a,b in levels[s][:3]) for s in levels)
    ordered=(all(Decimal(a[0])>Decimal(b[0]) for a,b in zip(known['bids'],known['bids'][1:]))
             and all(Decimal(a[0])<Decimal(b[0]) for a,b in zip(known['asks'],known['asks'][1:]))) if complete else False
    delta=(received-source).total_seconds()
    session='SSI_SESSION_UNKNOWN'
    if flag and flag.get('f')=='LO' and flag.get('day')==local.date().isoformat() and flag.get('connection')==observation['connection']:
        if time(9,15)<=local.time()<time(11,30):session='SSI_CONTINUOUS_AM'
        elif time(13)<=local.time()<time(14,30):session='SSI_CONTINUOUS_PM'
    reasons=[]
    if not complete or not ordered:reasons.append('PARTIAL_OR_INVALID_BOOK')
    if complete and Decimal(levels['bids'][0][0])>Decimal(levels['asks'][0][0]):reasons.append('CROSSED_BOOK')
    if not -5<=delta<=30:reasons.append('SOURCE_CLOCK_SKEW_OR_STALE')
    if contract['quote_semantics']!='FULL_SNAPSHOT':reasons.append('UNVERIFIED_SNAPSHOT_SEMANTICS')
    r=dict(provider='ssi',schema_version='ssi-live-observation-v2',symbol=p['s'],exchange='HOSE',board_id='SSI_MAIN_EQUITY',
        local_date=local.date().isoformat(),source_timestamp=source.isoformat(),source_time_raw=p.get('t'),
        source_timezone_status='ASSUMED_ASIA_HO_CHI_MINH',collected_at_utc=observation['received_at'],
        source_receive_clock_delta_ms=round(delta*1000),event_id=identity,unit_contract_hash=contract['hash'],
        price_unit='VND' if verified else 'SSI_SOURCE_PRICE',volume_unit='shares' if verified else 'SSI_SOURCE_QUANTITY',
        unit_verification_status='EMPIRICALLY_VERIFIED' if verified else 'UNVERIFIED_SSI_QUOTE',
        data_quality_status='INVALID' if reasons else 'VALID',market_session=session,market_flag=flag.get('f') if flag else None,issues=reasons,
        provided_slots={s:len(v) for s,v in levels.items()},observed_levels={s:len(v) for s,v in known.items()})
    for side,key in (('bid','bids'),('ask','asks')):
        for i in (1,2,3):
            pair=levels[key][i-1] if len(levels[key])>=i else [None,None]
            r[f'{side}_price_{i}'],r[f'{side}_vol_{i}']=pair
    return r,levels


def normalize_trade(observation,contract,identity):
    p=observation['payload'];source=source_time(p.get('t'))
    verified=contract.get('trade_status')=='EMPIRICALLY_VERIFIED' and p['s'] in contract.get('trade_symbols',[])
    price=number(p.get('p'),contract['trade_price_multiplier'] if verified else '1')
    qty=number(p.get('q'),contract['trade_volume_multiplier'] if verified else '1',True)
    if price is None or qty is None or qty<=0:raise ValueError('SSI_TRADE_VALUES_INVALID')
    return dict(id=identity,time=observation['received_at'],received_at=observation['received_at'],
        source_time=source.isoformat(),source_time_raw=p.get('t'),price=price,qty=qty,
        side={'B':'BUY','S':'SELL'}.get(p.get('si'),'UNSPECIFIED'),provider_side=p.get('si'),
        cumulative=number(p.get('v'),contract['trade_volume_multiplier'] if verified else '1',True),
        unit_status='EMPIRICALLY_VERIFIED' if verified else 'UNVERIFIED_SSI_TRADE',
        identity_policy='LOCAL_CAPTURE_ID_NOT_PROVIDER_TRADE_ID',retransmission_status='UNKNOWN')
