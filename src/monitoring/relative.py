"""Experimental scale-invariant episodes without granting unit certification.

Spread/mid and same-price depth ratios are invariant to positive, common price
and quantity factors. Absolute-size imbalance thresholds remain unavailable.
"""
import hashlib

from src.monitoring.spread import SpreadDetector, SpreadConfig, features, number, CONTINUOUS
from src.monitoring.events import EpisodeDetector, EventConfig

VERSION = 'relative-orderbook-v1'
SCOPE = 'RELATIVE_RATIOS_ONLY_UNVERIFIED_UNITS'
MAX_PATCHES = 30000
MAX_OBSERVATIONS = 25000


def relative_features(record):
    result = features(record)
    reason = None
    if (record.get('provider') != 'dnse' or record.get('price_unit') != 'VND'
            or record.get('volume_unit') != 'shares' or record.get('board_id') != 'G1'
            or record.get('unit_verification_status') not in
            ('ASSUMED_SHARED_SCHEMA', 'EMPIRICALLY_VERIFIED', 'OFFICIALLY_VERIFIED')
            or not record.get('unit_contract_hash')):
        reason = 'RELATIVE_SCHEMA_NOT_SUPPORTED'
    elif record.get('data_quality_status') == 'INVALID':
        reason = 'INVALID_OBSERVATION'
    elif record.get('market_session') not in CONTINUOUS:
        reason = 'OUTSIDE_CONTINUOUS_SESSION'
    else:
        for side in ('bid', 'ask'):
            levels = [(number(record.get(f'{side}_price_{i}')),
                       number(record.get(f'{side}_vol_{i}'), quantity=True)) for i in (1, 2, 3)]
            if any(p is None or q is None for p, q in levels) or levels[0][1] <= 0:
                reason = 'INCOMPLETE_RELATIVE_BOOK'
                break
            if any((levels[i][0] <= levels[i+1][0] if side == 'bid'
                    else levels[i][0] >= levels[i+1][0]) for i in (0, 1)):
                reason = 'INVALID_RELATIVE_LEVEL_ORDER'
                break
        if reason is None and (result['spread_bps'] is None
                or number(record['bid_price_1']) >= number(record['ask_price_1'])):
            reason = 'MISSING_OR_INVALID_BEST_QUOTES'
    result.update(eligible=reason is None, reason=reason)
    return result


class RelativeSpread(SpreadDetector):
    def input_features(self, record):
        return relative_features(record)


class RelativeEngine:
    def __init__(self, scope, spread_config=None, event_config=None):
        self.scope = scope + '|' + VERSION
        self.spread = RelativeSpread(self.scope, spread_config or SpreadConfig())
        self.config = event_config or EventConfig()
        self.detectors = {side: EpisodeDetector(self.scope, 'DEPTH_DROP', side, self.config)
                          for side in ('bid', 'ask')}
        self.patches = []
        self.config_hash = hashlib.sha256((VERSION + self.spread.config_hash +
            next(iter(self.detectors.values())).config_hash).encode()).hexdigest()

    def push(self, record, index):
        feature = self.spread.push(record, index)
        detectors = dict(spread=dict(state=feature['detector_state'], reason=feature.get('quality_reason')))
        for side, detector in self.detectors.items():
            detectors['depth_' + side] = detector.push(record, index, feature)
        detectors['imbalance'] = dict(state='INHIBITED', reason='ABSOLUTE_VOLUME_REQUIRES_VERIFIED_UNITS')
        self.patches = []
        for detector in [self.spread, *self.detectors.values()]:
            for patch in detector.patches:
                event = dict(patch['event'], analysis_basis=SCOPE, units_certified=False,
                             detector_version=VERSION)
                self.patches.append(dict(at_index=patch['at_index'], event=event))
            # Streaming the frames never retains a second full patch history.
            detector.patches.clear()
        relative_eligible = feature['eligible']
        feature.update(eligible=False, relative_eligible=relative_eligible, analysis_basis=SCOPE,
            reason='RELATIVE_ANALYSIS_ONLY' if relative_eligible else feature.get('reason'),
            detectors=detectors)
        return feature
