"""Scoped, evidenced WS top-three conversion. Never reconstruct absent levels."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from src.data_pipeline.dnse_normalization import canonical_json, market_session
from src.data_pipeline.dnse_ws_review import six_levels
from src.data_pipeline.validation import validate
from src.models.orderbook import LOCAL_TZ, OrderBookSnapshot, QuoteLevel

VERSION='dnse-ws-canonical-v1'
CONTRACT_PATH=Path(__file__).resolve().parents[2]/'config/unit_contract.dnse-ws-v1.json'


@dataclass(frozen=True)
class WsRules:
    payload:dict
    rule_hash:str


def load_ws_rules(path=CONTRACT_PATH):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    if (payload.get('format')!='dnse-ws-units-v1' or payload.get('provider')!='dnse'
            or payload.get('scope')!={'symbol':'ACB','market_id':'STO','board_id':'G1'}
            or payload.get('price_multiplier_to_vnd')!='1000'
            or payload.get('volume_multiplier_to_shares')!='10'
            or payload.get('policy')!='COMPLETE_TOP3_EACH_MESSAGE_NO_CARRY_FORWARD'
            or payload.get('full_ten_level_semantics_verified') is not False
            or payload.get('evidence',{}).get('distinct_matched_clocks',0)<20):
        raise ValueError('WS_UNIT_CONTRACT_UNREVIEWED')
    return WsRules(payload,hashlib.sha256(canonical_json(payload).encode()).hexdigest())


def canonical_top3(record,event_key,rules):
    """Return a separate canonical view only inside the reviewed scope."""
    scope=rules.payload['scope']
    if any(record.get(k)!=v for k,v in scope.items()):
        return None
    if not record.get('source_timestamp') or not record.get('source_unix_nanoseconds') or 'OUT_OF_PARTITION_SOURCE_DAY' in record.get('issues',[]):
        return None
    try:
        pairs=six_levels(record,'ws')
        source=datetime.fromisoformat(record['source_timestamp'])
        received=datetime.fromisoformat(record['collected_at_utc'])
        if source.tzinfo is None or received.tzinfo is None:
            return None
        # The ISO clock is deliberately floored to microseconds, not rounded.
        from src.data_pipeline.dnse_ws_normalization import timestamp
        _,iso_ns=timestamp(source.isoformat())
        if not int(iso_ns)<=int(record['source_unix_nanoseconds'])<int(iso_ns)+1000:
            return None
        if source.astimezone(LOCAL_TZ).date().isoformat()!=record.get('source_local_date'):
            return None
        built={'bid':[],'ask':[]}
        for side,index,price,qty in pairs:
            shares=qty*Decimal(rules.payload['volume_multiplier_to_shares'])
            if shares!=shares.to_integral_value():
                return None
            built[side].append(QuoteLevel(index,price*Decimal(rules.payload['price_multiplier_to_vnd']),int(shares)))
        book=validate(OrderBookSnapshot(symbol=scope['symbol'],exchange='HOSE',provider='dnse',
            upstream_source='dnse_websocket',capture_method='websocket_realtime',provider_version='dnse-ws-v1',
            collected_at_utc=received,source_timestamp=source,market_session=market_session(source,scope['market_id']),
            bids=tuple(built['bid']),asks=tuple(built['ask']),event_id='dnse-ws:'+event_key,
            unit_verification_status='EMPIRICALLY_VERIFIED',unit_contract_hash=rules.rule_hash))
    except (ValueError,TypeError,KeyError):
        return None
    result=book.to_record()
    result.update(normalization_version=VERSION,price_unit='VND',volume_unit='shares',
        market_id=scope['market_id'],board_id='G1',local_date=record['source_local_date'],
        source_local_time=source.astimezone(LOCAL_TZ).isoformat(),source_unix_nanoseconds=record['source_unix_nanoseconds'],
        verified_top3_observation=True,full_ten_level_semantics_verified=False,
        phase_basis='exchange_time_rule_not_live_status',
        financial_eligible=book.data_quality_status!='INVALID' and book.market_session in ('DNSE_CONTINUOUS_AM','DNSE_CONTINUOUS_PM'))
    for side,levels in built.items():
        result[side+'_observed_depth_shares']=sum(level.volume for level in levels)
        result[side+'_depth_is_partial']=False
    return result


def verify_canonical_scope(record,rules=None):
    rules=rules or load_ws_rules()
    if (record.get('normalization_version')!=VERSION or record.get('unit_contract_hash')!=rules.rule_hash
            or any(record.get(k)!=v for k,v in rules.payload['scope'].items())
            or record.get('unit_verification_status')!='EMPIRICALLY_VERIFIED'
            or record.get('verified_top3_observation') is not True
            or record.get('full_ten_level_semantics_verified') is not False
            or record.get('exchange')!='HOSE' or record.get('upstream_source')!='dnse_websocket'
            or record.get('capture_method')!='websocket_realtime'
            or record.get('price_unit')!='VND' or record.get('volume_unit')!='shares'):
        raise ValueError('WS_CANONICAL_CONTRACT_SCOPE_MISMATCH')
    for side in ('bid','ask'):
        for i in (1,2,3):
            price=record.get(f'{side}_price_{i}');qty=record.get(f'{side}_vol_{i}')
            if price is None or not isinstance(qty,int) or isinstance(qty,bool) or qty<0:
                raise ValueError('WS_CANONICAL_COMPLETE_TOP3_REQUIRED')
            price=Decimal(str(price))
            if not price.is_finite() or price<=0:
                raise ValueError('WS_CANONICAL_POSITIVE_PRICE_REQUIRED')
    source=datetime.fromisoformat(record['source_timestamp'])
    if source.tzinfo is None or record['market_session']!=market_session(source,record['market_id']):
        raise ValueError('WS_CANONICAL_SOURCE_SESSION_MISMATCH')
