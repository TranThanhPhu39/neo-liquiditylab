"""Deterministic spread episodes on verified, source-clock observations.

Historical source order is explicitly retrospective, never arrival-time backtesting.
Persistence means a chain of observations, not proof of the intervening market state.
"""
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from statistics import median

VERSION = 'spread-observation-v1'
VERIFIED = {'EMPIRICALLY_VERIFIED', 'OFFICIALLY_VERIFIED'}
CONTINUOUS = {'DNSE_CONTINUOUS_AM', 'DNSE_CONTINUOUS_PM','SSI_CONTINUOUS_AM','SSI_CONTINUOUS_PM'}


def stamp(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('MONITOR_TIMEZONE_REQUIRED')
    return value


def number(value, *, quantity=False):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    try:
        n = Decimal(value)
        if not n.is_finite() or n < 0 or (not quantity and n == 0):
            return None
        if quantity and n != n.to_integral_value():
            return None
        return int(n) if quantity else n
    except (InvalidOperation, ValueError):
        return None


def features(record):
    canonical=(record.get('price_unit')=='VND' and record.get('volume_unit')=='shares')
    diagnostic=(record.get('provider')=='ssi' and record.get('price_unit')=='SSI_SOURCE_PRICE'
                and record.get('volume_unit')=='SSI_SOURCE_QUANTITY' and record.get('unit_verification_status')=='UNVERIFIED_SSI_QUOTE')
    if record.get('provider') not in ('dnse','ssi') or not (canonical or diagnostic):
        raise ValueError('MONITOR_CANONICAL_DNSE_REQUIRED')
    result = dict(spread_bps=None, bid_depth=None, ask_depth=None, imbalance=None,
                  eligible=False, reason=None)
    for side in ('bid', 'ask'):
        levels = [(number(record.get(f'{side}_price_{i}')),
                   number(record.get(f'{side}_vol_{i}'), quantity=True)) for i in (1, 2, 3)]
        if all(p is not None and q is not None for p, q in levels):
            result[side + '_depth'] = sum(q for _, q in levels)
    bid, ask = (number(record.get(s + '_price_1')) for s in ('bid', 'ask'))
    bid_q, ask_q = (number(record.get(s + '_vol_1'), quantity=True) for s in ('bid', 'ask'))
    if bid is not None and ask is not None and bid <= ask and bid_q is not None and ask_q is not None:
        result['spread_bps'] = (ask - bid) / ((ask + bid) / 2) * 10000
    b, a = result['bid_depth'], result['ask_depth']
    if b is not None and a is not None and a + b > 0:
        result['imbalance'] = Decimal(b - a) / (b + a)
    if record.get('unit_verification_status') not in VERIFIED:
        result['reason'] = 'UNVERIFIED_UNITS'
    elif record.get('data_quality_status') == 'INVALID':
        result['reason'] = 'INVALID_OBSERVATION'
    elif record.get('market_session') not in CONTINUOUS:
        result['reason'] = 'OUTSIDE_CONTINUOUS_SESSION'
    elif result['spread_bps'] is None:
        result['reason'] = 'MISSING_OR_INVALID_BEST_QUOTES'
    else:
        result['eligible'] = True
    return result


@dataclass(frozen=True)
class SpreadConfig:
    baseline_seconds: int = 600
    min_samples: int = 60
    min_span_seconds: int = 120
    min_coverage: Decimal = Decimal('.10')
    multiplier: Decimal = Decimal('1.5')
    min_increase_bps: Decimal = Decimal('2')
    min_duration_seconds: int = 3
    min_observations: int = 3
    max_observation_gap_seconds: int = 30
    release_fraction: Decimal = Decimal('.8')
    end_duration_seconds: int = 3
    cooldown_seconds: int = 15

    def __post_init__(self):
        integer_fields = ('baseline_seconds', 'min_samples', 'min_span_seconds',
                          'min_duration_seconds', 'min_observations', 'max_observation_gap_seconds',
                          'end_duration_seconds', 'cooldown_seconds')
        for field in integer_fields:
            n = getattr(self, field)
            if type(n) is not int or n < 0:
                raise ValueError('MONITOR_INVALID_CONFIGURATION')
        if (self.baseline_seconds < 1 or self.min_samples < 2 or self.min_observations < 2
                or self.min_span_seconds >= self.baseline_seconds or self.max_observation_gap_seconds < 1):
            raise ValueError('MONITOR_INVALID_CONFIGURATION')
        for field in ('min_coverage', 'multiplier', 'min_increase_bps', 'release_fraction'):
            n = getattr(self, field)
            if not isinstance(n, Decimal) or not n.is_finite():
                raise ValueError('MONITOR_INVALID_CONFIGURATION')
        if not (0 <= self.min_coverage <= 1 and self.multiplier > 1
                and self.min_increase_bps > 0 and 0 < self.release_fraction < 1):
            raise ValueError('MONITOR_INVALID_CONFIGURATION')

    def payload(self):
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


class SpreadDetector:
    def __init__(self, scope, config=None):
        self.scope = scope
        self.config = config or SpreadConfig()
        self.config_hash = hashlib.sha256(json.dumps(self.config.payload(), sort_keys=True).encode()).hexdigest()
        self.samples = deque()
        self.previous = None
        self.active = None
        self.candidate = None
        self.recovering = None
        self.cooldown_until = 0
        self.patches = []

    def _patch(self, index, **changes):
        self.active.update(changes)
        # Copy so later changes cannot leak into earlier replay patches.
        self.patches.append(dict(at_index=index, event=dict(self.active)))

    def _interrupt(self, index, reason):
        if self.active is not None:
            self._patch(index, state='INTERRUPTED', end_reason=reason,
                        ended_at=self.previous['source_timestamp'], end_index=self.previous['index'])
        self.samples.clear()
        self.active = self.candidate = self.recovering = None
        self.cooldown_until = 0

    def push(self, record, index):
        t = stamp(record['source_timestamp']).timestamp()
        stamp(record['collected_at_utc'])
        f = features(record)
        session = record.get('market_session')
        identity = (record.get('provider'), record.get('symbol'), record.get('local_date'),
                    record.get('unit_contract_hash'), record.get('board_id'), session)
        reason = None
        if self.previous:
            if t < self.previous['t']:
                reason = 'OUT_OF_ORDER_SOURCE_TIME'
            elif identity != self.previous['identity']:
                reason = 'SCOPE_OR_SESSION_CHANGED'
            elif t - self.previous['t'] > self.config.max_observation_gap_seconds:
                reason = 'OBSERVATION_GAP'
        if not f['eligible']:
            reason = f['reason']
        if reason:
            self._interrupt(index, reason)
        previous = dict(t=t, identity=identity, source_timestamp=record['source_timestamp'], index=index)
        if not f['eligible'] or reason == 'OUT_OF_ORDER_SOURCE_TIME':
            self.previous = previous
            return dict(f, detector_state='INHIBITED', quality_reason=reason, baseline=None)
        cfg = self.config
        while self.samples and self.samples[0][0] < t - cfg.baseline_seconds:
            self.samples.popleft()
        baseline = None
        if self.samples:
            span = Decimal(str(t - self.samples[0][0]))
            coverage = Decimal(len(self.samples)) / max(Decimal(1), span)
            if len(self.samples) >= cfg.min_samples and span >= cfg.min_span_seconds and coverage >= cfg.min_coverage:
                m = median(s[1] for s in self.samples)
                threshold = max(m * cfg.multiplier, m + cfg.min_increase_bps)
                baseline = dict(median_bps=str(m), threshold_bps=str(threshold),
                                sample_seconds=len(self.samples), span_seconds=str(span),
                                last_source_time=self.samples[-1][2])
        spread = f['spread_bps']
        state = 'WARMING_UP' if baseline is None else 'NORMAL'
        if self.active:
            threshold = Decimal(self.active['threshold_bps'])
            peak = max(Decimal(self.active['peak_spread_bps']), spread)
            self._patch(index, last_observed_at=record['source_timestamp'],
                        last_index=index, peak_spread_bps=str(peak), last_spread_bps=str(spread))
            if spread <= threshold * cfg.release_fraction:
                if self.recovering is None:
                    self.recovering = t
                state = 'RECOVERING'
                self._patch(index, state='RECOVERING')
                if t - self.recovering >= cfg.end_duration_seconds:
                    self._patch(index, state='ENDED', ended_at=record['source_timestamp'],
                                end_index=index, end_reason='RETURNED_BELOW_RELEASE_THRESHOLD')
                    self.active = self.recovering = None
                    self.cooldown_until = t + cfg.cooldown_seconds
                    state = 'COOLDOWN'
            else:
                self.recovering = None
                state = 'ACTIVE'
                self._patch(index, state='ACTIVE')
        elif t < self.cooldown_until:
            state = 'COOLDOWN'
        else:
            if self.candidate:
                threshold = Decimal(self.candidate['baseline']['threshold_bps'])
            else:
                threshold = Decimal(baseline['threshold_bps']) if baseline else None
            if threshold is not None and spread > threshold:
                if self.candidate is None:
                    self.candidate = dict(t=t, index=index, id=record['event_id'],
                                          source_timestamp=record['source_timestamp'],
                                          baseline=dict(baseline), count=0, peak=spread)
                c = self.candidate
                c['count'] += 1
                c['peak'] = max(c['peak'], spread)
                state = 'CANDIDATE'
                if t - c['t'] >= cfg.min_duration_seconds and c['count'] >= cfg.min_observations:
                    event_id = hashlib.sha256((self.scope + '|' + VERSION + '|' + self.config_hash + '|' + c['id']).encode()).hexdigest()
                    self.active = dict(id=event_id, type='SPREAD_WIDENING', detector_version=VERSION,
                        config_hash=self.config_hash, state='ACTIVE', started_at=c['source_timestamp'],
                        detected_at=record['source_timestamp'], start_index=c['index'], detected_index=index,
                        last_index=index, last_observed_at=record['source_timestamp'], ended_at=None,
                        end_reason=None, threshold_bps=c['baseline']['threshold_bps'],
                        baseline=c['baseline'], peak_spread_bps=str(c['peak']), last_spread_bps=str(spread),
                        start_observation_id=c['id'], detection_observation_id=record['event_id'],
                        observations_at_detection=c['count'], evidence_scope=self.scope)
                    self._patch(index)
                    self.candidate = None
                    state = 'ACTIVE'
            else:
                self.candidate = None
        # One prior-observation sample per source second; freeze training during episodes.
        if self.active is None and self.candidate is None:
            item = (t, spread, record['source_timestamp'])
            if self.samples and int(self.samples[-1][0]) == int(t):
                self.samples[-1] = item
            else:
                self.samples.append(item)
        self.previous = previous
        return dict(f, detector_state=state, quality_reason=reason, baseline=baseline)


def events_at(patches, index):
    result = {}
    for patch in patches:
        if patch['at_index'] > index:
            break
        result[patch['event']['id']] = dict(patch['event'])
    return list(result.values())
