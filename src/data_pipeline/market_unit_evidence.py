"""Batch numerical unit checks against saved, explicitly labelled public rows.

Matches are review candidates, never permission to relabel units or run detectors.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from urllib.parse import urlsplit

from src.models.orderbook import LOCAL_TZ

CAPTION = 'Đơn vị giá: 1000 đồng - Đơn vị khối lượng: 1 cổ phiếu'
MARKETS = {'HO': 'HOSE', 'HSX': 'HOSE', 'HOSE': 'HOSE',
           'HA': 'HNX', 'HN': 'HNX', 'HNX': 'HNX', 'UP': 'UPCOM', 'UPCOM': 'UPCOM'}
MARKET_IDS = {'HOSE': 'STO', 'HNX': 'STX', 'UPCOM': 'UPX'}


def aware(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('UNIT_REFERENCE_TIMEZONE_REQUIRED')
    return value


def decimal(value, *, displayed=False, quantity=False):
    if isinstance(value, bool) or value is None:
        raise ValueError('UNIT_REFERENCE_NUMBER_INVALID')
    try:
        number = Decimal(str(value).strip().replace(',', '') if displayed else str(value))
        if not number.is_finite() or number < 0 or not quantity and number == 0:
            raise ValueError()
        if quantity and number != number.to_integral_value():
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('UNIT_REFERENCE_NUMBER_INVALID') from None


def reference_url(url):
    value = urlsplit(url)
    return (value.scheme == 'https' and value.hostname == 'priceboard.maybank-kimeng.com.vn'
            and value.port in (None, 443) and not value.username and not value.password)


def read_maybank(value):
    if not reference_url(value.get('url', '')):
        raise ValueError('UNIT_REFERENCE_ORIGIN_INVALID')
    references, rejected = [], []
    for ordinal, sample in enumerate(value.get('samples', [])):
        if not reference_url(sample.get('url', '')) or CAPTION not in sample.get('text', ''):
            rejected.append({'sample': ordinal, 'reason': 'EXPLICIT_REFERENCE_UNITS_REQUIRED'})
            continue
        try:
            when = aware(sample['received_at'])
        except (KeyError, ValueError, TypeError):
            rejected.append({'sample': ordinal, 'reason': 'INVALID_REFERENCE_CLOCK'})
            continue
        for row in sample.get('rows', []):
            cells = [str(c).strip() for c in row.get('cells', [])]
            symbol = cells[0] if cells else None
            try:
                if len(cells) < 21 or cells[1] not in MARKETS or not symbol or not symbol.isalnum():
                    raise ValueError('REFERENCE_ROW_LAYOUT_UNREVIEWED')
                sides = {}
                for side, positions in [('bids', (9, 7, 5)), ('asks', (15, 17, 19))]:
                    levels = [(decimal(cells[i], displayed=True) * 1000,
                               decimal(cells[i+1], displayed=True, quantity=True)) for i in positions]
                    if len({p for p, _ in levels}) != 3 or not any(q > 0 for _, q in levels):
                        raise ValueError('NONZERO_DISTINCT_REFERENCE_LEVELS_REQUIRED')
                    if any((levels[i][0] <= levels[i+1][0] if side == 'bids'
                            else levels[i][0] >= levels[i+1][0]) for i in (0, 1)):
                        raise ValueError('REFERENCE_LEVEL_ORDER_INVALID')
                    sides[side] = levels
                if sides['bids'][0][0] >= sides['asks'][0][0]:
                    raise ValueError('REFERENCE_CROSSED_OR_LOCKED_BOOK')
                references.append(dict(symbol=symbol, exchange=MARKETS[cells[1]], received_at=when.isoformat(),
                    day=when.astimezone(LOCAL_TZ).date().isoformat(), sample=ordinal, **sides))
            except (ValueError, IndexError, TypeError) as error:
                rejected.append({'sample': ordinal, 'symbol': symbol, 'reason': str(error)})
    return references, rejected


def signature(sides):
    # Decimal normalisation makes 100 and 100.00 the same numeric state.
    return tuple((p.normalize(), q.normalize()) for side in ('bids', 'asks') for p, q in sides[side])


def compare(symbol, day, exchange, records, references, *, max_seconds=5):
    if max_seconds not in (5, 20):
        raise ValueError('UNIT_REFERENCE_WINDOW_UNREVIEWED')
    refs = [r for r in references if (r['symbol'], r['day'], r['exchange']) == (symbol, day, exchange)]
    result = dict(symbol=symbol, day=day, exchange=exchange, reference_rows=len(refs),
                  distinct_books=0, matched_pairs=[], status='REFERENCE_NOT_CAPTURED',
                  unit_status_changed=False, detector_permission_granted=False)
    if not refs:
        return result
    by_book = defaultdict(list)
    for record in records:
        if (record.get('provider'), record.get('provider_version'), record.get('board_id'),
            record.get('market_id'), record.get('exchange'), record.get('symbol'), record.get('local_date'),
            record.get('price_unit'), record.get('volume_unit')) != (
                'dnse', '2026-07-23', 'G1', MARKET_IDS[exchange], exchange, symbol, day, 'VND', 'shares'):
            continue
        if record.get('data_quality_status') == 'INVALID' or not record.get('event_id') or not record.get('unit_contract_hash'):
            continue
        try:
            source = aware(record['source_timestamp'])
            if source.astimezone(LOCAL_TZ).date().isoformat() != day:
                continue
            sides = {side: [(decimal(record.get(f'{prefix}_price_{i}')),
                             decimal(record.get(f'{prefix}_vol_{i}'), quantity=True)) for i in (1, 2, 3)]
                     for side, prefix in [('bids', 'bid'), ('asks', 'ask')]}
            by_book[signature(sides)].append((source, record))
        except (ValueError, TypeError, KeyError):
            continue
    distinct, used_ids, hashes = set(), set(), set()
    for ref in refs:
        book = signature(ref)
        when = aware(ref['received_at'])
        candidates = [(abs((source-when).total_seconds()), record) for source, record in by_book.get(book, [])
                      if abs((source-when).total_seconds()) <= max_seconds]
        if not candidates:
            continue
        distance, record = min(candidates, key=lambda item: item[0])
        if book in distinct or record['event_id'] in used_ids:
            continue
        distinct.add(book)
        used_ids.add(record['event_id'])
        hashes.add(record['unit_contract_hash'])
        result['matched_pairs'].append(dict(event_id=record['event_id'], source_timestamp=record['source_timestamp'],
            reference_received_at=ref['received_at'], sample=ref['sample'], distance_seconds=distance,
            matched_levels=6, canonical_book_sha256=hashlib.sha256(str(book).encode()).hexdigest(),
            original_unit_status=record.get('unit_verification_status')))
    result.update(distinct_books=len(distinct), unit_rule_hashes=sorted(hashes),
        timing_scope='FIVE_SECOND_COMPARISON' if max_seconds == 5 else 'DELAYED_UNIT_COMPARISON_NOT_TIMING_PARITY')
    result['status'] = ('MIXED_UNIT_RULES_REVIEW_REQUIRED' if len(hashes) > 1 else
                        'MATCHED_PENDING_REVIEW' if len(distinct) >= 2 else
                        'INSUFFICIENT_DISTINCT_BOOKS' if distinct else 'NO_ALIGNED_NUMERIC_MATCH')
    return result
