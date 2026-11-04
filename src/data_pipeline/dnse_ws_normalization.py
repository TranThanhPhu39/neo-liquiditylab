"""Typed WS normalization, deliberately separate from REST unit verification."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from src.models.orderbook import LOCAL_TZ

VERSION = 'dnse-ws-structured-v1'
MARKETS = {'STO': 'HOSE', 'STX': 'HNX', 'UPX': 'UPCOM'}


def numeric(value):
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError()
        return str(number)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('WS_INVALID_SOURCE_NUMBER') from None


def timestamp(value):
    """Retain exact Unix nanoseconds; the ISO field is floored to microseconds."""
    try:
        if isinstance(value, dict):
            sec = value.get('Seconds', value.get('seconds'))
            nano = value.get('Nanos', value.get('nanos', 0))
            if isinstance(sec, bool) or isinstance(nano, bool):
                return None, None
            sec, nano = Decimal(str(sec)), Decimal(str(nano))
            if not sec.is_finite() or sec != sec.to_integral_value() or not nano.is_finite() or nano != nano.to_integral_value() or not 0 <= nano < 10**9:
                return None, None
            ns = int(sec)*10**9 + int(nano)
        elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            n = Decimal(str(value))
            if not n.is_finite():
                return None, None
            exact = n*(10**6 if n > 10**12 else 10**9)
            if exact != exact.to_integral_value():
                return None, None
            ns = int(exact)
        elif isinstance(value, str):
            at = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if at.tzinfo is None:
                return None, None
            at = at.astimezone(timezone.utc)
            delta = at-datetime(1970, 1, 1, tzinfo=timezone.utc)
            ns = (delta.days*86400+delta.seconds)*10**9+delta.microseconds*1000
        else:
            return None, None
        seconds, nanos = divmod(ns, 10**9)
        at = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos//1000)
        return at.isoformat(), str(ns)
    except (ValueError, TypeError, OverflowError, OSError, InvalidOperation):
        return None, None


def normalize_event(payload, received_at, symbol, market, source_day):
    if (not re.fullmatch('[A-Z0-9]{1,12}', symbol) or payload.get('symbol')!=symbol
            or market not in MARKETS or payload.get('marketId')!=market
            or payload.get('boardId')!='G1' or payload.get('T')!='q'):
        raise ValueError('WS_NORMALIZATION_SCOPE_MISMATCH')
    received = datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        raise ValueError('WS_RECEIPT_TIMEZONE_REQUIRED')
    observed, nanoseconds = timestamp(payload.get('time'))
    day = datetime.fromisoformat(observed).astimezone(LOCAL_TZ).date().isoformat() if observed else None
    issues = ['UNVERIFIED_WEBSOCKET_UNITS', 'UNCONFIRMED_WS_MESSAGE_SEMANTICS']
    if not observed:
        issues.append('SOURCE_TIME_UNKNOWN')
    elif day != source_day:
        issues.append('OUT_OF_PARTITION_SOURCE_DAY')
    if observed and datetime.fromisoformat(observed)>received:
        issues.append('SOURCE_CLOCK_AHEAD_OF_RECEIPT')
    record = dict(normalization_version=VERSION, provider='dnse', upstream_source='dnse_websocket',
        capture_method='websocket_realtime', symbol=symbol, exchange=MARKETS[market], market_id=market,
        board_id='G1', source_timestamp=observed, source_unix_nanoseconds=nanoseconds,
        source_local_date=day, collected_at_utc=received.astimezone(timezone.utc).isoformat(),
        normalization_stage='STRUCTURE_ONLY', message_semantics='UNCONFIRMED_INDEPENDENT_MESSAGE',
        unit_verification_status='UNVERIFIED_WEBSOCKET', price_unit=None, volume_unit=None,
        price_multiplier=None, volume_multiplier=None, unit_contract_hash=None, financial_eligible=False)
    partial = False
    for source, side in (('bid', 'bid'), ('offer', 'ask')):
        levels = payload.get(source)
        if levels is not None and (not isinstance(levels, list) or len(levels)>100):
            raise ValueError('WS_LEVEL_ARRAY_INVALID')
        record[side+'_source_state'] = 'NULL_OR_MISSING' if levels is None else 'EMPTY_ARRAY' if not levels else 'PRESENT'
        built = []
        for i, item in enumerate(levels or [], 1):
            if not isinstance(item, dict):
                raise ValueError('WS_LEVEL_OBJECT_INVALID')
            price = numeric(item.get('price'))
            quantities = {key: numeric(item[key]) for key in ('qtty', 'quantity') if key in item}
            if len(quantities)==2 and quantities['qtty']!=quantities['quantity']:
                raise ValueError('WS_QUANTITY_ALIAS_CONFLICT')
            qty = next(iter(quantities.values()), None)
            if price is None or qty is None:
                partial = True
            if price is not None and Decimal(price)==0:
                issues.append('ZERO_OR_AUCTION_SOURCE_PRICE:'+side+':'+str(i))
            built.append(dict(level=i, source_price=price, source_quantity=qty,
                              quantity_fields=list(quantities)))
        record[side+'_levels'] = built
        if len(built)<3:
            partial = True
        # Never attach VND/share labels or copy the REST multipliers onto WS values.
        for i in (1, 2, 3):
            record[f'{side}_price_{i}'] = None
            record[f'{side}_vol_{i}'] = None
    record['source_total_bid_quantity'] = numeric(payload.get('totalBidQtty'))
    record['source_total_ask_quantity'] = numeric(payload.get('totalOfferQtty'))
    record['data_quality_status'] = 'PARTIAL' if partial else 'STRUCTURED_UNVERIFIED'
    record['issues'] = issues
    return record
