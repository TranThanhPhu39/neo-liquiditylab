"""Research must not fabricate trades, confirmed daily highs or future evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from src.monitoring.breakout import analyze
from src.monitoring.breakout_store import research


BASE = datetime(2026, 11, 11, 3, tzinfo=timezone.utc)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def trade(seconds, price, n):
    return dict(id=str(n), time=at(seconds), price=str(price), qty=100, cumulative=n * 100)


def pack(rows=None):
    return dict(symbol='ACB', day='2026-11-11', price_unit='VND', volume_unit='shares',
                unit_status='EMPIRICALLY_VERIFIED_FOR_ACB_TRADES',
                trades=rows if rows is not None else [trade(-30, 100, 1)] + [trade(i, 101 if i == 0 else 102, i // 30 + 2) for i in range(0, 901, 30)])


def frame(seconds, **changes):
    return dict(id='quote'+str(seconds), time=at(seconds), quality='VALID', units='EMPIRICALLY_VERIFIED',
                session='DNSE_CONTINUOUS_AM', bids=[['100', 300], ['99', 100], ['98', 100]],
                asks=[['101', 100], ['102', 100], ['103', 100]], **changes)


def run(p=None, frames=None, cutoff=900, reference=None):
    return analyze(pack=p or pack(), frames=frames if frames is not None else [frame(i) for i in range(-300, 901, 10)],
                   reference=reference or dict(price='100', day='2026-11-10', kind='USER_SUPPLIED_PREVIOUS_HIGH'),
                   day='2026-11-11', symbol='ACB', exchange='HOSE', cutoff=at(cutoff))


def test_crossing_uses_executions_and_endpoint_not_daily_close_confirmation():
    r = run()
    assert len(r['episodes']) == 1
    e = r['episodes'][0]
    assert e['time'] == at(0) and e['price'] == '101'
    assert e['book_group'] == 'POSITIVE_IMBALANCE_TIGHT_SPREAD'
    assert e['book']['time'] == at(0) and e['book']['baseline_spread_bps'] is not None
    for minutes in ('5', '15'):
        o = e['outcomes'][minutes]
        assert o['state'] == 'OBSERVED_ENDPOINT' and o['above_reference'] is True
    assert r['groups'][0]['horizons']['15']['eligible'] == 1
    assert r['quality']['reference_is_official'] is False


def test_rewinding_hides_future_trades_outcomes_and_future_quote_baseline():
    early = run(cutoff=60)
    assert early['episodes'][0]['outcomes']['5']['state'] == 'PENDING'
    assert early['quality']['trades_to_cutoff'] == 4
    assert all(b['time'] <= at(60) for b in early['chart'])
    changed = [frame(i) for i in range(-300, 1, 10)] + [dict(frame(1), asks=[['500', 100], ['501', 100], ['502', 100]])]
    assert run(frames=changed, cutoff=60)['episodes'][0]['book'] == early['episodes'][0]['book']
    assert run(cutoff=-1)['episodes'] == []


def test_starting_above_does_not_establish_a_crossing_or_use_quotes_as_price():
    assert run(pack([trade(0, 101, 1), trade(30, 102, 2)]))['episodes'] == []
    assert run(pack([trade(0, 99, 1), trade(30, 100, 2)]))['episodes'] == []
    p = pack([])
    assert run(p)['state'] == 'NO_OBSERVED_TRADES'


def test_endpoint_can_be_below_reference_without_relabelling_a_false_breakout_signal():
    p = pack()
    for t in p['trades'][2:]:
        t['price'] = '99'
    e = run(p)['episodes'][0]
    assert e['outcomes']['5']['above_reference'] is False
    assert e['outcomes']['5']['return_bps'] < 0


def test_sparse_window_and_missing_endpoint_excluded_from_descriptive_comparison():
    sparse = pack([trade(-30, 100, 1), trade(0, 101, 2), trade(300, 102, 3)])
    r = run(sparse)
    assert r['episodes'][0]['outcomes']['5']['state'] == 'PARTIAL_WINDOW'
    assert r['episodes'][0]['outcomes']['15']['state'] == 'MISSING_ENDPOINT'
    assert r['groups'][0]['horizons']['5']['eligible'] == 0


def test_cumulative_gap_does_not_claim_complete_window_volume():
    p = pack()
    p['trades'][4]['cumulative'] += 100
    r = run(p)
    assert r['episodes'][0]['outcomes']['5']['state'] == 'PARTIAL_WINDOW'


@pytest.mark.parametrize('change', [dict(units='ASSUMED_SHARED_SCHEMA'), dict(quality='INVALID'),
                                 dict(session='DNSE_AUCTION'), dict(bids=[['100', 100]])])
def test_unverified_invalid_auction_or_incomplete_book_remains_unavailable(change):
    frames = [dict(frame(i), **change) for i in range(-300, 1, 10)]
    e = run(frames=frames)['episodes'][0]
    assert e['book'] is None and e['book_group'] == 'UNAVAILABLE'
    assert e['outcomes']['5']['state'] == 'OBSERVED_ENDPOINT'


def test_stale_book_or_too_few_distinct_baseline_seconds_not_promoted():
    assert run(frames=[frame(-20)])['episodes'][0]['book'] is None
    e = run(frames=[frame(-1), frame(0)])['episodes'][0]
    assert e['book'] is not None and e['book_group'] == 'UNAVAILABLE'


def test_cooldown_prevents_overlapping_five_and_fifteen_minute_windows():
    p = pack([trade(-30, 100, 1), trade(0, 101, 2), trade(30, 100, 3), trade(60, 101, 4)])
    r = run(p)
    assert len(r['episodes']) == 1 and r['quality']['cooldown_excluded'] == 1


def test_lunch_boundary_not_bridged_and_first_afternoon_trade_above_not_crossing():
    p = pack([dict(trade(0, 100, 1), time='2026-11-11T04:25:00Z'),
              dict(trade(30, 101, 2), time='2026-11-11T04:25:30Z'),
              dict(trade(60, 102, 3), time='2026-11-11T06:00:00Z')])
    r = analyze(pack=p, frames=[], reference=dict(price='100'), day='2026-11-11', symbol='ACB',
                exchange='HOSE', cutoff='2026-11-11T07:00:00Z')
    assert len(r['episodes']) == 1
    assert r['episodes'][0]['outcomes']['5']['state'] == 'SESSION_BOUNDARY'


def test_units_and_reference_fail_closed():
    assert run(dict(pack(), unit_status='ASSUMED_SHARED_SCHEMA'))['state'] == 'TRADE_UNITS_UNVERIFIED'
    assert run(dict(pack(), price_unit='SSI_SOURCE_PRICE'))['state'] == 'TRADE_UNITS_UNAVAILABLE'
    assert run(reference=dict(price=None))['state'] == 'REFERENCE_REQUIRED'
    with pytest.raises(ValueError, match='CUTOFF_DAY'):
        analyze(pack=pack(), frames=[], reference=dict(price='100'), day='2026-11-11', symbol='ACB',
                exchange='HOSE', cutoff='2026-11-12T03:00:00Z')


def test_duplicate_and_out_of_order_execution_identity_rejected():
    t = trade(0, 100, 1)
    with pytest.raises(ValueError, match='DUPLICATE'):
        run(pack([t, t]))
    with pytest.raises(ValueError, match='OUT_OF_ORDER'):
        run(pack([trade(30, 100, 1), trade(0, 101, 2)]))


def store(tmp_path):
    entries = [dict(dataset_id=str(i)*64, day=d, symbol='ACB', exchange='HOSE')
               for i, d in enumerate(('2026-11-10', '2026-11-11'), 1)]
    path = tmp_path/'book.json'
    path.write_text(json.dumps(dict(frames=[frame(i) for i in range(-300, 901, 10)])))
    prior = dict(pack([dict(trade(0, 100, 1), time='2026-11-10T03:00:00Z')]), day='2026-11-10')
    return SimpleNamespace(index=lambda: entries, trades=lambda did: deepcopy(prior if did == entries[0]['dataset_id'] else pack()),
                           dataset=lambda did: path, root=tmp_path)


def test_store_reference_uses_only_prior_day_trade_high_and_keeps_provisional_label(tmp_path):
    r = research(store(tmp_path), '2'*64, at(900))
    assert r['reference']['price'] == '100' and r['reference']['kind'] == 'STORED_TRADE_HIGH'
    assert r['reference']['official'] is False and r['reference']['day'] == '2026-11-10'
    assert len(r['episodes']) == 1
    r = research(store(tmp_path), '2'*64, at(900), '103')
    assert r['reference']['kind'] == 'USER_SUPPLIED_PREVIOUS_HIGH' and r['episodes'] == []


def test_store_never_silently_skips_a_missing_previous_day(tmp_path):
    s = store(tmp_path)
    s.index = lambda: [dict(dataset_id='2'*64, day='2026-11-11', symbol='ACB', exchange='HOSE'),
                       dict(dataset_id='1'*64, day='2026-11-09', symbol='ACB', exchange='HOSE')]
    assert research(s, '2'*64, at(900))['state'] == 'REFERENCE_REQUIRED'
    with pytest.raises(ValueError, match='MUST_BE_PAST'):
        research(s, '2'*64, at(900), '100', '2026-11-11')
