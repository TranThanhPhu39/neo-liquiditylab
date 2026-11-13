"""Offline observation episodes with frozen evidence, never trade/cancel inference."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json

from src.monitoring.spread import SpreadConfig, SpreadDetector, number, stamp

VERSION = 'offline-monitor-v2'


@dataclass(frozen=True)
class EventConfig:
    depth_drop: Decimal = Decimal('.50')
    depth_release: Decimal = Decimal('.25')
    min_common_levels: int = 2
    min_reference_fraction: Decimal = Decimal('.50')
    imbalance_threshold: Decimal = Decimal('.80')
    imbalance_release: Decimal = Decimal('.60')
    min_total_depth: int = 10000
    duration_seconds: int = 5
    min_observations: int = 3
    end_duration_seconds: int = 3
    cooldown_seconds: int = 15
    max_gap_seconds: int = 30

    def __post_init__(self):
        for name in ('depth_drop', 'depth_release', 'min_reference_fraction', 'imbalance_threshold', 'imbalance_release'):
            n = getattr(self, name)
            if not isinstance(n, Decimal) or not n.is_finite() or not 0 <= n <= 1:
                raise ValueError('MONITOR_INVALID_EVENT_CONFIGURATION')
        if not (0 < self.depth_drop < 1 and self.depth_release < self.depth_drop
                and 0 < self.imbalance_threshold < 1 and self.imbalance_release < self.imbalance_threshold):
            raise ValueError('MONITOR_INVALID_EVENT_CONFIGURATION')
        for name in ('min_common_levels', 'min_total_depth', 'duration_seconds', 'min_observations',
                     'end_duration_seconds', 'cooldown_seconds', 'max_gap_seconds'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError('MONITOR_INVALID_EVENT_CONFIGURATION')
        if not 1 <= self.min_common_levels <= 3 or self.min_observations < 2 or self.max_gap_seconds < 1:
            raise ValueError('MONITOR_INVALID_EVENT_CONFIGURATION')

    def payload(self):
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


class EpisodeDetector:
    def __init__(self, scope, kind, side, config):
        self.scope, self.kind, self.side, self.config = scope, kind, side, config
        self.config_hash = hashlib.sha256(json.dumps(config.payload(), sort_keys=True).encode()).hexdigest()
        self.previous = self.candidate = self.active = self.recovery = None
        self.cooldown_until = 0
        self.patches = []

    def patch(self, index, **changes):
        self.active.update(changes)
        self.patches.append(dict(at_index=index, event=dict(self.active)))

    def interrupt(self, index, reason):
        if self.active:
            self.patch(index, state='INTERRUPTED', end_reason=reason,
                       ended_at=self.previous['record']['source_timestamp'], end_index=self.previous['index'])
        self.active = self.candidate = self.recovery = None
        self.cooldown_until = 0

    def levels(self, record):
        values = {}
        for i in (1, 2, 3):
            p = number(record.get(f'{self.side}_price_{i}'))
            q = number(record.get(f'{self.side}_vol_{i}'), quantity=True)
            if p is None or q is None:
                return None
            key = format(p.normalize(), 'f')
            if key in values:
                return None
            values[key] = q
        return values

    def measure(self, record, feature, reference):
        if self.kind == 'IMBALANCE_PERSISTENCE':
            if feature['bid_depth'] is None or feature['ask_depth'] is None or feature['imbalance'] is None:
                return dict(reason='INCOMPLETE_DEPTH')
            total = feature['bid_depth'] + feature['ask_depth']
            if total < self.config.min_total_depth:
                return dict(reason='INSUFFICIENT_TOTAL_DEPTH')
            direction = 'bid' if feature['imbalance'] >= 0 else 'ask'
            if reference and reference['side'] != direction:
                return dict(reason='IMBALANCE_DIRECTION_CHANGED')
            return dict(value=abs(feature['imbalance']), threshold=self.config.imbalance_threshold,
                        release=self.config.imbalance_release,
                        reference=reference or dict(side=direction, source_time=record['source_timestamp'],
                                                    observation_id=record['event_id']),
                        baseline=dict(method='FIXED_EXPERIMENTAL_THRESHOLD', threshold=str(self.config.imbalance_threshold)),
                        evidence=dict(side=direction, bid_depth=feature['bid_depth'], ask_depth=feature['ask_depth'],
                                      signed_imbalance=str(feature['imbalance']), total_depth=total))
        current = self.levels(record)
        if current is None:
            return dict(reason='INCOMPLETE_DEPTH')
        if reference is None:
            if not self.previous or self.previous['levels'] is None:
                return dict(reason='NO_PREVIOUS_COMPARABLE_BOOK')
            previous = self.previous
            reference = dict(levels=previous['levels'], source_time=previous['record']['source_timestamp'],
                             observation_id=previous['record']['event_id'], index=previous['index'])
        before = reference['levels']
        common = set(before) & set(current)
        ref_total = sum(before.values())
        ref_common = sum(before[p] for p in common)
        if (len(common) < self.config.min_common_levels or ref_total <= 0 or ref_common <= 0
                or Decimal(ref_common) / ref_total < self.config.min_reference_fraction):
            return dict(reason='INSUFFICIENT_COMMON_PRICE_COVERAGE')
        after = sum(current[p] for p in common)
        drop = Decimal(ref_common - after) / ref_common
        return dict(value=drop, threshold=self.config.depth_drop, release=self.config.depth_release,
                    reference=reference,
                    baseline=dict(method='PREVIOUS_OBSERVATION_COMMON_PRICES', source_time=reference['source_time'],
                                  observation_id=reference['observation_id'], reference_depth=ref_common),
                    evidence=dict(side=self.side, reference_depth=ref_common, current_depth=after,
                                  drop_fraction=str(drop), reference_total_depth=ref_total,
                                  common_prices=sorted(common, key=Decimal),
                                  out_of_observation_prices=sorted(set(before) - set(current), key=Decimal),
                                  newly_observed_prices=sorted(set(current) - set(before), key=Decimal)))

    def push(self, record, index, feature):
        t = stamp(record['source_timestamp']).timestamp()
        identity = tuple(record.get(k) for k in ('provider', 'symbol', 'local_date', 'unit_contract_hash', 'market_session'))
        reason = feature.get('reason') if not feature['eligible'] else None
        if self.previous:
            if t < self.previous['time']:
                reason = 'OUT_OF_ORDER_SOURCE_TIME'
            elif identity != self.previous['identity']:
                reason = 'SCOPE_OR_SESSION_CHANGED'
            elif t - self.previous['time'] > self.config.max_gap_seconds:
                reason = 'OBSERVATION_GAP'
        if reason:
            self.interrupt(index, reason)
        old = self.previous
        # Ineligible observations must not become reference books.
        valid_previous = dict(time=t, identity=identity, record=record, index=index,
                              levels=self.levels(record) if feature['eligible'] and self.side else None)
        if not feature['eligible'] or reason == 'OUT_OF_ORDER_SOURCE_TIME':
            self.previous = valid_previous
            return dict(state='INHIBITED', reason=reason)
        if reason:
            self.previous = None
        reference = (self.active or self.candidate or {}).get('reference')
        value = self.measure(record, feature, reference)
        if value.get('reason'):
            self.interrupt(index, value['reason'])
            self.previous = valid_previous
            return dict(state='WARMING_UP' if value['reason']=='NO_PREVIOUS_COMPARABLE_BOOK' else 'INHIBITED', reason=value['reason'])
        metric = value['value']
        state = 'NORMAL'
        if self.active:
            self.patch(index, last_index=index, last_observed_at=record['source_timestamp'],
                       peak_value=str(max(Decimal(self.active['peak_value']), metric)),
                       current_value=str(metric), evidence=dict(value['evidence']))
            if metric <= value['release']:
                if self.recovery is None:
                    self.recovery = t
                self.patch(index, state='RECOVERING')
                state = 'RECOVERING'
                if t-self.recovery >= self.config.end_duration_seconds:
                    self.patch(index, state='ENDED', ended_at=record['source_timestamp'], end_index=index,
                               end_reason='RETURNED_BELOW_RELEASE_THRESHOLD')
                    self.active = self.recovery = None
                    self.cooldown_until = t+self.config.cooldown_seconds
                    state = 'COOLDOWN'
            else:
                self.recovery = None
                self.patch(index, state='ACTIVE')
                state = 'ACTIVE'
        elif t < self.cooldown_until:
            state = 'COOLDOWN'
        elif metric >= value['threshold']:
            if self.candidate is None:
                self.candidate = dict(time=t, index=index, id=record['event_id'], started_at=record['source_timestamp'],
                                      reference=value['reference'], baseline=value['baseline'], count=0, peak=metric)
            c = self.candidate
            c['count'] += 1
            c['peak'] = max(c['peak'], metric)
            state = 'CANDIDATE'
            if t-c['time'] >= self.config.duration_seconds and c['count'] >= self.config.min_observations:
                eid = hashlib.sha256((self.scope+VERSION+self.kind+str(self.side)+self.config_hash+c['id']).encode()).hexdigest()
                self.active = dict(id=eid, type=self.kind, side=value['evidence']['side'], detector_version=VERSION,
                    config_hash=self.config_hash, evidence_scope=self.scope, state='ACTIVE',
                    started_at=c['started_at'], start_index=c['index'], detected_at=record['source_timestamp'],
                    detected_index=index, last_index=index, last_observed_at=record['source_timestamp'],
                    ended_at=None, end_reason=None, reference=c['reference'], baseline=c['baseline'],
                    threshold=str(value['threshold']), current_value=str(metric), peak_value=str(c['peak']),
                    observations_at_detection=c['count'], evidence=dict(value['evidence']),
                    start_observation_id=c['id'], detection_observation_id=record['event_id'])
                self.patch(index)
                self.candidate = None
                state = 'ACTIVE'
        else:
            self.candidate = None
        self.previous = valid_previous
        return dict(state=state, reason=reason, value=str(metric), threshold=str(value['threshold']))


class OfflineEngine:
    def __init__(self, scope, spread_config=None, event_config=None):
        self.spread = SpreadDetector(scope, spread_config or SpreadConfig())
        self.config = event_config or EventConfig()
        self.detectors = {
            'depth_bid': EpisodeDetector(scope, 'DEPTH_DROP', 'bid', self.config),
            'depth_ask': EpisodeDetector(scope, 'DEPTH_DROP', 'ask', self.config),
            'imbalance': EpisodeDetector(scope, 'IMBALANCE_PERSISTENCE', None, self.config),
        }
        self.patches = []
        self.positions = {k:0 for k in ['spread', *self.detectors]}
        self.config_hash = hashlib.sha256(json.dumps(dict(spread=self.spread.config.payload(), events=self.config.payload()), sort_keys=True).encode()).hexdigest()

    def interrupt(self,index,reason):
        self.spread._interrupt(index,reason)
        self.spread.previous=None
        for detector in self.detectors.values():
            detector.interrupt(index,reason);detector.previous=None
        for name,detector in [('spread',self.spread),*self.detectors.items()]:
            self.patches.extend(detector.patches[self.positions[name]:])
            self.positions[name]=len(detector.patches)

    def push(self, record, index):
        result = self.spread.push(record, index)
        result['detectors'] = dict(spread=dict(state=result['detector_state'], reason=result.get('quality_reason')))
        for name, detector in self.detectors.items():
            result['detectors'][name] = detector.push(record,index,result)
        for name, detector in [('spread',self.spread), *self.detectors.items()]:
            self.patches.extend(detector.patches[self.positions[name]:])
            self.positions[name] = len(detector.patches)
        return result


def parameters(spread=None, events=None):
    s,e=spread or SpreadConfig(),events or EventConfig()
    return dict(spread_multiplier=str(s.multiplier), min_increase_bps=str(s.min_increase_bps),
                depth_drop=str(e.depth_drop), imbalance_threshold=str(e.imbalance_threshold),
                duration_seconds=e.duration_seconds)


def configs_from_parameters(value):
    allowed=set(parameters())
    if not isinstance(value,dict) or set(value)!=allowed:
        raise ValueError('MONITOR_INVALID_PARAMETERS')
    p={}
    for k in allowed-{'duration_seconds'}:
        if not isinstance(value[k],(str,int)) or isinstance(value[k],bool):
            raise ValueError('MONITOR_INVALID_PARAMETERS')
        try:p[k]=Decimal(str(value[k]))
        except InvalidOperation:raise ValueError('MONITOR_INVALID_PARAMETERS') from None
        if not p[k].is_finite():raise ValueError('MONITOR_INVALID_PARAMETERS')
    d=value['duration_seconds']
    if (type(d) is not int or not 1<=d<=120 or not Decimal('1.1')<=p['spread_multiplier']<=10
            or not Decimal('.1')<=p['min_increase_bps']<=1000 or not Decimal('.1')<=p['depth_drop']<=Decimal('.95')
            or not Decimal('.5')<=p['imbalance_threshold']<=Decimal('.99')):
        raise ValueError('MONITOR_INVALID_PARAMETERS')
    return (SpreadConfig(multiplier=p['spread_multiplier'],min_increase_bps=p['min_increase_bps'],min_duration_seconds=d),
            EventConfig(depth_drop=p['depth_drop'],depth_release=p['depth_drop']/2,
                        imbalance_threshold=p['imbalance_threshold'],imbalance_release=p['imbalance_threshold']*Decimal('.75'),
                        duration_seconds=d))
