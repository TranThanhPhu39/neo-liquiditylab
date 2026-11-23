"""Retrospective, cutoff-safe research on executions crossing a fixed reference.

Observed trade highs are explicitly not official daily highs. Quote changes never
stand in for executions. Endpoints describe observed prices, not executable P&L.
"""
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from statistics import median

from src.monitoring.spread import stamp

VERSION = 'previous-high-research-v1'
MAX_TRADES = 200000
MAX_BOOKS = 100000
MAX_EPISODES = 100
COOLDOWN_SECONDS = 900
MAX_TRADE_GAP_SECONDS = 60
MAX_ENDPOINT_AGE_SECONDS = 30
MAX_BOOK_AGE_SECONDS = 15
VERIFIED = {'EMPIRICALLY_VERIFIED', 'OFFICIALLY_VERIFIED'}


def positive(value, zero=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and (n >= 0 if zero else n > 0) else None
    except (InvalidOperation, ValueError):
        return None


def segment(when, exchange):
    local = when.astimezone(timezone(timedelta(hours=7)))
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    start = 9 * 3600 + (900 if exchange == 'HOSE' else 0)
    if start <= seconds < 11 * 3600 + 1800:
        return (local.date().isoformat(), 'AM')
    if 13 * 3600 <= seconds < 14 * 3600 + (3600 if exchange == 'UPCOM' else 1800):
        return (local.date().isoformat(), 'PM')
    return None


def prepare_trades(pack, day, cutoff):
    rows = pack.get('trades', [])
    if len(rows) > MAX_TRADES:
        raise ValueError('RESEARCH_TRADE_LIMIT')
    out = []
    seen = set()
    invalid = 0
    last = None
    for row in rows:
        try:
            t = stamp(row['time'])
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid += 1
            continue
        if last is not None and t < last:
            raise ValueError('RESEARCH_TRADES_OUT_OF_ORDER')
        last = t
        if t.astimezone(timezone(timedelta(hours=7))).date().isoformat() != day:
            raise ValueError('RESEARCH_TRADE_DAY_MISMATCH')
        if t > cutoff:
            continue
        price, qty = positive(row.get('price')), positive(row.get('qty'))
        identity = row.get('id')
        if price is None or qty is None or price > 1000000000 or qty > 9000000000000000 or qty != qty.to_integral_value() or not identity:
            invalid += 1
            continue
        signature = (t, price, qty, positive(row.get('cumulative'), zero=True))
        if identity in seen:
            raise ValueError('RESEARCH_DUPLICATE_TRADE_ID')
        seen.add(identity)
        out.append(dict(time=t, price=price, qty=int(qty), cumulative=signature[3], id=identity))
    return out, invalid


def book_metrics(frame):
    if frame.get('quality') == 'INVALID':
        return None
    if frame.get('units') not in VERIFIED:
        return None
    try:
        b, a = frame['bids'][:3], frame['asks'][:3]
        if len(b) != 3 or len(a) != 3:
            return None
        bp = [positive(p) for p, q in b]
        ap = [positive(p) for p, q in a]
        bq = [positive(q, zero=True) for p, q in b]
        aq = [positive(q, zero=True) for p, q in a]
        if any(v is None for v in bp + ap + bq + aq):
            return None
        if not (bp[0] > bp[1] > bp[2] and ap[0] < ap[1] < ap[2] and bp[0] < ap[0]):
            return None
        bd, ad = sum(bq), sum(aq)
        if bd + ad <= 0:
            return None
        return dict(spread_bps=float((ap[0] - bp[0]) / ((ap[0] + bp[0]) / 2) * 10000),
                    bid_depth=int(bd), ask_depth=int(ad), imbalance=float((bd - ad) / (bd + ad)))
    except (KeyError, TypeError, ValueError):
        return None


def analyze(*, pack, frames, reference, day, symbol, exchange, cutoff):
    point = stamp(cutoff)
    if point.astimezone(timezone(timedelta(hours=7))).date().isoformat() != day:
        raise ValueError('RESEARCH_CUTOFF_DAY_MISMATCH')
    if pack.get('symbol') != symbol or pack.get('day') != day:
        raise ValueError('RESEARCH_TRADE_SCOPE_MISMATCH')
    result = dict(format=VERSION, symbol=symbol, day=day, cutoff=point.isoformat(),
                  reference=reference, episodes=[], groups=[], chart=[], state='READY',
                  scope='RETROSPECTIVE_OBSERVED_EXECUTIONS_NOT_TRADING_SIGNALS',
                  config=dict(horizons_minutes=[5, 15], cooldown_seconds=COOLDOWN_SECONDS,
                              max_trade_gap_seconds=MAX_TRADE_GAP_SECONDS,
                              max_endpoint_age_seconds=MAX_ENDPOINT_AGE_SECONDS,
                              max_book_age_seconds=MAX_BOOK_AGE_SECONDS,
                              book_rule='IMBALANCE_GT_ZERO_AND_SPREAD_LE_PAST_10_MINUTE_MEDIAN',
                              baseline_min_distinct_seconds=5))
    if not pack.get('trades'):
        result['state'] = 'NO_OBSERVED_TRADES'
        return result
    if pack.get('price_unit') != 'VND' or pack.get('volume_unit') != 'shares':
        result['state'] = 'TRADE_UNITS_UNAVAILABLE'
        return result
    if pack.get('unit_status') not in {
            'EMPIRICALLY_VERIFIED_FOR_ACB_TRADES', 'EMPIRICALLY_VERIFIED', 'OFFICIALLY_VERIFIED',
            'CUMULATIVE_SCALE_AND_MATCH_INCREMENT_RECONCILED'}:
        result['state'] = 'TRADE_UNITS_UNVERIFIED'
        return result
    trades, invalid = prepare_trades(pack, day, point)
    result['quality'] = dict(trades_to_cutoff=len(trades), invalid_trades=invalid,
                             provider_sequence_completeness='UNKNOWN', reference_is_official=False,
                             trade_unit_status=pack.get('unit_status'),
                             trade_price_unit_status=pack.get('price_unit_status', 'NOT_SEPARATELY_CERTIFIED'))
    if not trades:
        result['state'] = 'NO_OBSERVED_TRADES'
        return result
    threshold = positive(reference.get('price'))
    if threshold is None:
        result['state'] = 'REFERENCE_REQUIRED'
        return result
    books = []
    for count, frame in enumerate(frames):
        if count >= MAX_BOOKS:
            raise ValueError('RESEARCH_BOOK_LIMIT')
        t = stamp(frame['time'])
        if t > point:
            break
        if books and t < books[-1][0]:
            raise ValueError('RESEARCH_BOOKS_OUT_OF_ORDER')
        # Keep only small causal features, never a second full replay in RAM.
        books.append((t, dict(metrics=book_metrics(frame), id=frame.get('id'),
                              session=frame.get('session'))))
    book_times = [b[0] for b in books]
    times = [t['time'] for t in trades]
    previous = None
    last_cross = None
    total_crossings = 0
    cooldown_excluded = 0
    for i, trade in enumerate(trades):
        t, price = trade['time'], trade['price']
        block = segment(t, exchange)
        if block is None:
            previous = None
            continue
        same = previous is not None and segment(previous['time'], exchange) == block
        crossed = same and previous['price'] <= threshold < price
        # A capture starting above the level does not establish a crossing.
        if not crossed:
            previous = trade
            continue
        total_crossings += 1
        gap = (t - previous['time']).total_seconds()
        previous = trade
        if last_cross is not None and (t - last_cross).total_seconds() < COOLDOWN_SECONDS:
            cooldown_excluded += 1
            continue
        if len(result['episodes']) >= MAX_EPISODES:
            result['state'] = 'RESOURCE_LIMIT_PARTIAL'
            break
        last_cross = t
        episode = dict(id=hashlib.sha256((VERSION + '|' + symbol + '|' + str(threshold) + '|' + trade['id']).encode()).hexdigest(),
                       time=t.isoformat(), price=str(price), trade_id=trade['id'],
                       crossing_status='OBSERVED_CROSSING' if gap <= MAX_TRADE_GAP_SECONDS else 'SPARSE_OBSERVED_CROSSING',
                       prior_trade_gap_seconds=gap, book=None, book_group='UNAVAILABLE', outcomes={})
        bi = bisect_right(book_times, t) - 1
        if bi >= 0:
            bt, bf = books[bi]
            metrics = bf['metrics']
            session_valid = bf.get('session') in {'DNSE_CONTINUOUS_AM', 'DNSE_CONTINUOUS_PM'}
            if metrics and session_valid and segment(bt, exchange) == block and (t - bt).total_seconds() <= MAX_BOOK_AGE_SECONDS:
                # At most one sample per second, from the past only.
                samples = {}
                j = bi - 1
                while j >= 0 and (t - books[j][0]).total_seconds() <= 600:
                    st, sf = books[j]
                    sm = sf['metrics']
                    if st < t and segment(st, exchange) == block and sm and sf.get('session') == bf.get('session'):
                        samples.setdefault(int(st.timestamp()), sm['spread_bps'])
                    j -= 1
                baseline = median(samples.values()) if len(samples) >= 5 else None
                episode['book'] = dict(metrics, time=bt.isoformat(), age_seconds=(t - bt).total_seconds(),
                                       observation_id=bf.get('id'), baseline_spread_bps=baseline,
                                       baseline_seconds=len(samples), baseline_cutoff=t.isoformat())
                if baseline is not None:
                    episode['book_group'] = 'POSITIVE_IMBALANCE_TIGHT_SPREAD' if metrics['imbalance'] > 0 and metrics['spread_bps'] <= baseline else 'OTHER_OBSERVED_BOOK'
        for minutes in (5, 15):
            target = t + timedelta(minutes=minutes)
            outcome = dict(state='PENDING', target=target.isoformat())
            if segment(target, exchange) != block:
                outcome['state'] = 'SESSION_BOUNDARY'
            elif point >= target:
                end = bisect_right(times, target) - 1
                tail = trades[end]
                if (target - tail['time']).total_seconds() > MAX_ENDPOINT_AGE_SECONDS:
                    outcome['state'] = 'MISSING_ENDPOINT'
                else:
                    window = trades[i:end+1]
                    maximum_gap = max(((b['time'] - a['time']).total_seconds() for a, b in zip(window, window[1:])), default=0)
                    cumulative = all(a['cumulative'] is not None and b['cumulative'] is not None
                                     and b['cumulative'] - a['cumulative'] == b['qty']
                                     for a, b in zip(window, window[1:]))
                    full = maximum_gap <= MAX_TRADE_GAP_SECONDS and cumulative and gap <= MAX_TRADE_GAP_SECONDS and not invalid
                    outcome.update(state='OBSERVED_ENDPOINT' if full else 'PARTIAL_WINDOW',
                                   endpoint_time=tail['time'].isoformat(), price=str(tail['price']),
                                   above_reference=tail['price'] > threshold,
                                   return_bps=float((tail['price'] / price - 1) * 10000),
                                   observed_volume_shares=sum(x['qty'] for x in window[1:]),
                                   max_trade_gap_seconds=maximum_gap,
                                   cumulative_increments_reconciled=cumulative)
            episode['outcomes'][str(minutes)] = outcome
        result['episodes'].append(episode)
    # Per-minute observed OHLC, never held forward across an empty minute.
    bars = {}
    for row in trades:
        if segment(row['time'], exchange) is None:
            continue
        key = row['time'].replace(second=0, microsecond=0).isoformat()
        bar = bars.setdefault(key, dict(time=key, open=str(row['price']), high=str(row['price']), low=str(row['price']), close=str(row['price']), volume=0))
        bar.update(high=str(max(Decimal(bar['high']), row['price'])), low=str(min(Decimal(bar['low']), row['price'])), close=str(row['price']))
        bar['volume'] += row['qty']
    result['chart'] = list(bars.values())
    for key in ('ALL', 'POSITIVE_IMBALANCE_TIGHT_SPREAD', 'OTHER_OBSERVED_BOOK', 'UNAVAILABLE'):
        selected = [e for e in result['episodes'] if key == 'ALL' or e['book_group'] == key]
        group = dict(group=key, episodes=len(selected), horizons={})
        for minutes in ('5', '15'):
            values = [e['outcomes'][minutes] for e in selected if e['outcomes'][minutes]['state'] == 'OBSERVED_ENDPOINT']
            group['horizons'][minutes] = dict(eligible=len(values), above_reference=sum(v['above_reference'] for v in values),
                                             median_return_bps=median(v['return_bps'] for v in values) if values else None)
        result['groups'].append(group)
    result['quality'].update(observed_crossings=total_crossings, cooldown_excluded=cooldown_excluded,
                             max_episodes=MAX_EPISODES)
    return result
