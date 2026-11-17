from copy import deepcopy
from datetime import timedelta

import pytest

from src.data_pipeline.market_unit_evidence import CAPTION, aware, compare, read_maybank
from tests.test_event_monitor import record


def quote(n=0, **changes):
    return record(n, symbol='VNM', provider_version='2026-07-23', market_id='STO',
                  unit_verification_status='ASSUMED_SHARED_SCHEMA', **changes)


def board(records):
    samples = []
    for r in records:
        cells = [''] * 21
        cells[:2] = [r['symbol'], {'HOSE': 'HO', 'HNX': 'HN', 'UPCOM': 'UP'}[r['exchange']]]
        for prefix, positions in [('bid', (9, 7, 5)), ('ask', (15, 17, 19))]:
            for level, at in enumerate(positions, 1):
                from decimal import Decimal
                cells[at] = str(Decimal(r[f'{prefix}_price_{level}']) / 1000)
                cells[at+1] = f"{r[f'{prefix}_vol_{level}']:,}"
        samples.append(dict(url='https://priceboard.maybank-kimeng.com.vn/?lang=vi-vn',
                            text=CAPTION, received_at=r['source_timestamp'], rows=[dict(cells=cells)]))
    return dict(url='https://priceboard.maybank-kimeng.com.vn/', samples=samples)


def check(records, value=None, **kwargs):
    refs, errors = read_maybank(value or board(records))
    assert not errors
    return compare('VNM', '2026-11-11', 'HOSE', records, refs, **kwargs)


def test_two_independent_book_states_are_candidates_without_changing_units():
    records = [quote(), quote(2, bid_vol_1=350)]
    original = deepcopy(records)
    result = check(records)
    assert result['status'] == 'MATCHED_PENDING_REVIEW' and result['distinct_books'] == 2
    assert not result['unit_status_changed'] and not result['detector_permission_granted']
    assert records == original


def test_repeated_or_zero_states_cannot_certify_units():
    records = [quote(), quote(2)]
    assert check(records)['status'] == 'INSUFFICIENT_DISTINCT_BOOKS'
    value = board(records)
    for sample in value['samples']:
        for index in (6, 8, 10, 16, 18, 20):
            sample['rows'][0]['cells'][index] = '0'
    refs, errors = read_maybank(value)
    assert refs == [] and len(errors) == 2


@pytest.mark.parametrize('change', [dict(symbol='FPT'), dict(exchange='HNX', market_id='STX'),
    dict(board_id='G4'), dict(provider_version='other'), dict(data_quality_status='INVALID'),
    dict(local_date='2026-11-12'), dict(unit_contract_hash=None)])
def test_numeric_matches_cannot_cross_scope(change):
    r = quote(); value = board([r]); r.update(change)
    assert check([r], value)['status'] == 'NO_ALIGNED_NUMERIC_MATCH'


def test_missing_caption_wrong_scale_and_incomplete_quotes_are_not_accepted():
    r = quote(); value = board([r]); value['samples'][0]['text'] = ''
    assert read_maybank(value)[0] == []
    value = board([r]); value['samples'][0]['rows'][0]['cells'][10] = '10'
    assert check([r], value)['status'] == 'NO_ALIGNED_NUMERIC_MATCH'
    value = board([r]); r['bid_vol_2'] = None
    assert check([r], value)['status'] == 'NO_ALIGNED_NUMERIC_MATCH'


def test_delayed_evidence_is_not_reported_as_near_time_parity():
    records = [quote(), quote(2, bid_vol_1=350)]
    value = board(records)
    for sample in value['samples']:
        sample['received_at'] = (aware(sample['received_at']) + timedelta(seconds=10)).isoformat()
    assert check(records, value)['status'] == 'NO_ALIGNED_NUMERIC_MATCH'
    result = check(records, value, max_seconds=20)
    assert result['status'] == 'MATCHED_PENDING_REVIEW'
    assert result['timing_scope'] == 'DELAYED_UNIT_COMPARISON_NOT_TIMING_PARITY'
    assert not result['detector_permission_granted']
    for sample in value['samples']:
        sample['received_at'] = (aware(sample['received_at']) + timedelta(days=1)).isoformat()
    assert check(records, value, max_seconds=20)['status'] == 'REFERENCE_NOT_CAPTURED'


def test_mixed_rule_hashes_require_review():
    records = [quote(), quote(2, bid_vol_1=350)]
    records[1]['unit_contract_hash'] = 'different-rule'
    assert check(records)['status'] == 'MIXED_UNIT_RULES_REVIEW_REQUIRED'


def test_reference_origin_and_aware_clocks_are_required():
    value = board([quote()]); value['url'] = 'https://example.org/'
    with pytest.raises(ValueError, match='ORIGIN'):
        read_maybank(value)
    value = board([quote()]); value['samples'][0]['received_at'] = '2026-11-11T09:15:00'
    assert read_maybank(value)[0] == []


@pytest.mark.parametrize('exchange,market', [('HNX', 'STX'), ('UPCOM', 'UPX')])
def test_other_exchanges_require_their_own_matching_reference(exchange, market):
    r = quote(); r.update(exchange=exchange, market_id=market)
    refs, errors = read_maybank(board([r]))
    assert not errors
    result = compare('VNM', '2026-11-11', exchange, [r], refs)
    assert result['distinct_books'] == 1
