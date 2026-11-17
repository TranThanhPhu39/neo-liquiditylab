from copy import deepcopy
from decimal import Decimal

import pytest

from src.monitoring.relative import RelativeEngine, relative_features, SCOPE
from src.monitoring.spread import features, SpreadConfig
from src.monitoring.events import EventConfig
from src.monitoring.market import MarketHistory
from tests.test_event_monitor import record, config


def observations():
    return [record(i, ask='10020' if i >= 8 else '10002', symbol='VNM',
                   unit_verification_status='ASSUMED_SHARED_SCHEMA',
                   **{f'bid_vol_{j}': (j*10 if i >= 15 else j*100) for j in (1, 2, 3)})
            for i in range(25)]


def run(rows):
    engine = RelativeEngine('fixture', config(), EventConfig(duration_seconds=2, min_observations=3))
    frames, patches = [], []
    for index, row in enumerate(rows):
        frames.append(engine.push(row, index));patches.extend(deepcopy(engine.patches))
    events = {p['event']['id']: p['event'] for p in patches}
    return frames, list(events.values()), patches


@pytest.mark.parametrize('price_factor,quantity_factor', [('1', 1), ('1000', 10), ('.001', 100)])
def test_relative_event_decisions_are_invariant_to_common_positive_unit_factors(price_factor, quantity_factor):
    rows = observations();original = deepcopy(rows)
    expected_frames, expected_events, _ = run(rows)
    for row in rows:
        for side in ('bid', 'ask'):
            for i in (1, 2, 3):
                row[f'{side}_price_{i}'] = str(Decimal(row[f'{side}_price_{i}']) * Decimal(price_factor))
                row[f'{side}_vol_{i}'] *= quantity_factor
    frames, events, _ = run(rows)
    decision = lambda items: [(e['id'], e['type'], e['detected_index'], e['state']) for e in items]
    assert decision(events) == decision(expected_events)
    assert {'SPREAD_WIDENING', 'DEPTH_DROP'} <= {e['type'] for e in events}
    assert all(not f['eligible'] and f['relative_eligible'] for f in frames)
    assert all(not features(r)['eligible'] for r in rows)
    assert all(e['analysis_basis'] == SCOPE and e['units_certified'] is False for e in events)
    assert all(f['detectors']['imbalance']['state'] == 'INHIBITED' for f in frames)
    assert original[0]['unit_verification_status'] == rows[0]['unit_verification_status'] == 'ASSUMED_SHARED_SCHEMA'


@pytest.mark.parametrize('changes', [dict(data_quality_status='INVALID'), dict(bid_vol_2=None),
    dict(bid_vol_1=0), dict(bid_price_2='10000'), dict(ask_price_1='9999'),
    dict(market_session='DNSE_MIDDAY_BREAK'), dict(unit_contract_hash=None), dict(board_id='G4')])
def test_relative_analysis_never_opens_invalid_incomplete_or_wrong_scope_books(changes):
    row = observations()[0];row.update(changes)
    assert not relative_features(row)['eligible']
    frames, events, _ = run([dict(row, event_id=f'event-{i}') for i in range(5)])
    assert not events and all(not f['relative_eligible'] for f in frames)


def test_future_observations_do_not_change_the_known_prefix_and_bad_data_interrupts():
    rows = observations();_, _, prefix = run(rows[:12]);_, _, full = run(rows)
    assert prefix == [p for p in full if p['at_index'] < 12]
    rows[12]['data_quality_status'] = 'INVALID'
    frames, _, patches = run(rows)
    assert not frames[12]['relative_eligible']
    assert any(p['event'].get('end_reason') == 'INVALID_OBSERVATION' for p in patches)


def test_large_observation_gaps_reset_relative_baseline():
    rows = observations()
    rows[9]['source_timestamp'] = '2026-11-11T03:00:00+00:00'
    frames, _, _ = run(rows[:10])
    assert frames[-1]['quality_reason'] == 'OBSERVATION_GAP'
    assert frames[-1]['baseline'] is None


def test_market_relative_replay_has_distinct_identity_and_preserves_original_units(tmp_path, monkeypatch):
    history = MarketHistory(tmp_path)
    rows = [record(i*2, symbol='VNM', ask='10020' if i >= 90 else '10002',
                   unit_verification_status='ASSUMED_SHARED_SCHEMA') for i in range(105)]
    original = deepcopy(rows)
    monkeypatch.setattr(history, 'records', lambda entry: iter(rows))
    entry = dict(dataset_id='a'*64, symbol='VNM', exchange='HOSE', day='2026-11-11',
                 observations=len(rows), source_archive={}, quality={}, unit_rule_hash='rule')
    pack = history.replay(entry)
    assert pack['dataset_id'] != entry['dataset_id'] and pack['source_dataset_id'] == entry['dataset_id']
    assert pack['detector_scope'] == SCOPE and pack['event_patches']
    assert all(f['units'] == 'ASSUMED_SHARED_SCHEMA' and not f['features']['eligible'] for f in pack['frames'])
    assert history.summary(entry)['events']
    assert rows == original


def test_analysis_resource_limit_keeps_every_observation_and_marks_fallback(tmp_path, monkeypatch):
    import src.monitoring.market as module
    history = MarketHistory(tmp_path)
    rows = [record(i*2, symbol='VNM', ask='10020' if i >= 90 else '10002',
                   unit_verification_status='ASSUMED_SHARED_SCHEMA') for i in range(105)]
    monkeypatch.setattr(history, 'records', lambda entry: iter(rows))
    monkeypatch.setattr(module, 'MAX_PATCHES', 0)
    entry = dict(dataset_id='a'*64, symbol='VNM', exchange='HOSE', day='2026-11-11',
                 observations=len(rows), source_archive={}, quality={}, unit_rule_hash='rule')
    pack = history.replay(entry)
    assert len(pack['frames']) == len(rows) and pack['event_patches'] == []
    assert pack['detector_scope'] == 'OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION'
    assert pack['quality_summary']['relative_analysis_status'] == 'RESOURCE_LIMIT_OBSERVATION_FALLBACK'
