"""Exact DNSE scaling with explicit empirical scope and immutable raw provenance."""
from dataclasses import dataclass,replace
from datetime import datetime,time,timezone
from decimal import Decimal,InvalidOperation
import hashlib
import json

from src.data_pipeline.validation import validate
from src.models.orderbook import LOCAL_TZ,OrderBookSnapshot,QuoteLevel

NORMALIZATION_VERSION='dnse-canonical-v1'
MARKETS={'STO':'HOSE','STX':'HNX','UPX':'UPCOM'}


def canonical_json(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


@dataclass(frozen=True)
class DnseRules:
    api_version:str
    price_multiplier:Decimal
    volume_multiplier:Decimal
    verified_symbols:tuple
    verified_markets:tuple
    rule_hash:str
    payload:dict


def load_rules(path):
    record=json.loads(path.read_text(encoding='utf-8-sig'))
    if record.get('provider')!='dnse' or record.get('api_version')!='2026-07-23' or record.get('board_id')!='G1':
        raise ValueError('DNSE_RULE_PROVIDER_VERSION_BOARD_MISMATCH')
    price=Decimal(record['price_multiplier_to_vnd'])
    volume=Decimal(record['volume_multiplier_to_shares'])
    if price!=Decimal(1000) or volume!=Decimal(10):
        raise ValueError('DNSE_RULE_MULTIPLIER_UNREVIEWED')
    symbols=tuple(record['empirically_verified_symbols'])
    markets=tuple(record['empirically_verified_market_ids'])
    if not symbols or not markets:
        raise ValueError('DNSE_RULE_EMPIRICAL_SCOPE_MISSING')
    for evidence in record['evidence']:
        artifact=(path.parent/evidence['path']).resolve()
        if hashlib.sha256(artifact.read_bytes()).hexdigest()!=evidence['sha256']:
            raise ValueError('DNSE_UNIT_EVIDENCE_CHECKSUM_MISMATCH')
    digest=hashlib.sha256(canonical_json(record).encode()).hexdigest()
    return DnseRules(record['api_version'],price,volume,symbols,markets,digest,record)


def source_time(value):
    # The pinned official OpenAPI spec defines this field as GMT+7.
    if not isinstance(value,str):
        raise ValueError('DNSE_SOURCE_TIMESTAMP_MISSING')
    return datetime.strptime(value,'%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=LOCAL_TZ)


def market_session(at,market):
    local=at.astimezone(LOCAL_TZ)
    clock=local.time().replace(tzinfo=None)
    if local.weekday()>=5:
        return 'DNSE_CALENDAR_WEEKEND'
    start=time(9,15) if market=='STO' else time(9)
    end=time(15) if market=='UPX' else time(14,30)
    if start<=clock<time(11,30):
        return 'DNSE_CONTINUOUS_AM'
    if time(13)<=clock<end:
        return 'DNSE_CONTINUOUS_PM'
    if time(11,30)<=clock<time(13):
        return 'DNSE_MIDDAY_BREAK'
    return 'DNSE_OUTSIDE_CONTINUOUS'


def normalize_quote(quote,received_at,rules):
    symbol=quote.get('symbol')
    market=quote.get('marketId')
    if market not in MARKETS or quote.get('boardId')!='G1' or not isinstance(symbol,str) or not symbol.isalnum():
        raise ValueError('DNSE_RAW_QUOTE_SCOPE_INVALID')
    observed=source_time(quote.get('time'))
    received=datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        raise ValueError('DNSE_RECEIPT_TIMESTAMP_TIMEZONE_MISSING')
    issues=[]
    invalid=False
    verified=symbol in rules.verified_symbols and market in rules.verified_markets
    units='EMPIRICALLY_VERIFIED' if verified else 'ASSUMED_SHARED_SCHEMA'
    if not verified:
        issues.append('UNVERIFIED_DNSE_UNIT_SCOPE')

    def numeric(value,multiplier,label,volume=False):
        nonlocal invalid
        if value is None:
            issues.append('MISSING:'+label)
            return None
        try:
            if isinstance(value,bool):
                raise ValueError()
            raw=Decimal(str(value))
            number=raw*multiplier
            if not number.is_finite() or number<0 or volume and number!=number.to_integral_value():
                raise ValueError()
            if not volume and number==0:
                issues.append('ZERO_OR_AUCTION_PRICE:'+label)
                return None
            return int(number) if volume else number
        except (InvalidOperation,ValueError,TypeError):
            issues.append('INVALID_DNSE:'+label)
            invalid=True
            return None

    metadata={}
    sides={}
    for api_side,side in (('bid','bid'),('offer','ask')):
        levels=quote.get(api_side)
        if levels is not None and not isinstance(levels,list):
            raise ValueError('DNSE_LEVEL_ARRAY_INVALID')
        metadata[side+'_source_state']='NULL_OR_MISSING' if levels is None else 'EMPTY_ARRAY' if not levels else 'PRESENT'
        metadata[side+'_source_levels']=len(levels) if levels is not None else None
        if levels is None or not levels:
            issues.append('UNKNOWN_OR_EMPTY_SIDE:'+side)
        if levels and len(levels)>3:
            issues.append('MORE_THAN_THREE_SOURCE_LEVELS:'+side)
        built=[]
        for index in range(3):
            if not levels or index>=len(levels):
                built.append(QuoteLevel(index+1,None,None))
                continue
            item=levels[index]
            if not isinstance(item,dict):
                raise ValueError('DNSE_LEVEL_OBJECT_INVALID')
            built.append(QuoteLevel(index+1,numeric(item.get('price'),rules.price_multiplier,
                f'{side}_price_{index+1}'),numeric(item.get('quantity'),rules.volume_multiplier,
                f'{side}_vol_{index+1}',volume=True)))
        sides[side]=tuple(built)
        known=[level for level in built if level.usable]
        metadata[side+'_observed_depth_shares']=sum(level.volume for level in known) if known else None
        metadata[side+'_depth_is_partial']=len(known)<3
    aggregate_inconsistent=[]
    for key,side in (('totalBidQtty','bid'),('totalOfferQtty','ask')):
        raw_total=quote.get(key)
        try:
            total=Decimal(str(raw_total))*rules.volume_multiplier
            depth=metadata[side+'_observed_depth_shares']
            if total.is_finite() and depth is not None and total<depth:
                aggregate_inconsistent.append(key)
        except (InvalidOperation,TypeError,ValueError):
            aggregate_inconsistent.append(key)
    metadata['ignored_source_aggregate_fields']=aggregate_inconsistent
    identity=hashlib.sha256(canonical_json(quote).encode()).hexdigest()
    snapshot=OrderBookSnapshot(symbol=symbol,exchange=MARKETS[market],provider='dnse',
        upstream_source='dnse_openapi',capture_method='historical_rest_backfill',provider_version=rules.api_version,
        collected_at_utc=received.astimezone(timezone.utc),source_timestamp=observed.astimezone(timezone.utc),
        market_session=market_session(observed,market),bids=sides['bid'],asks=sides['ask'],
        event_id='dnse:'+identity,issues=tuple(issues),raw={},unit_verification_status=units,
        unit_contract_hash=rules.rule_hash)
    snapshot=validate(snapshot)
    if invalid:
        snapshot=replace(snapshot,data_quality_status='INVALID')
    record=snapshot.to_record()
    record.update(metadata,normalization_version=NORMALIZATION_VERSION,price_unit='VND',volume_unit='shares',
        market_id=market,board_id='G1',source_local_time=observed.isoformat(),local_date=observed.date().isoformat(),
        phase_basis='exchange_time_rule_not_live_status',
        financial_eligible=verified and snapshot.data_quality_status!='INVALID'
            and snapshot.market_session in ('DNSE_CONTINUOUS_AM','DNSE_CONTINUOUS_PM')
            and any(level.usable for level in snapshot.bids+snapshot.asks))
    return record


def snapshot_from_record(record):
    if record.get('provider')!='dnse' or record.get('normalization_version')!=NORMALIZATION_VERSION:
        raise ValueError('DNSE_CANONICAL_RECORD_REQUIRED')
    def side(name):
        return tuple(QuoteLevel(i,Decimal(record[f'{name}_price_{i}']) if record.get(f'{name}_price_{i}') is not None else None,
                         record.get(f'{name}_vol_{i}')) for i in range(1,4))
    return OrderBookSnapshot(symbol=record['symbol'],exchange=record['exchange'],provider='dnse',
        upstream_source=record['upstream_source'],capture_method=record['capture_method'],
        provider_version=record['provider_version'],collected_at_utc=datetime.fromisoformat(record['collected_at_utc']),
        source_timestamp=datetime.fromisoformat(record['source_timestamp']),market_session=record['market_session'],
        bids=side('bid'),asks=side('ask'),issues=tuple(record['issues']),data_quality_status=record['data_quality_status'],
        event_id=record['event_id'],unit_verification_status=record['unit_verification_status'],
        unit_contract_hash=record['unit_contract_hash'])
