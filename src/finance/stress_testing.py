from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR

from src.finance.execution_simulator import simulate_execution, rate
from src.models.orderbook import QuoteLevel
from src.models.scenario import Scenario


def apply_scenario(snapshot, scenario: Scenario):
    """Floor each bid volume to whole shares; shift both sides without tick rounding."""
    multiplier = 1 + scenario.price_shock
    def transform(level, bid=False):
        volume = level.volume
        if bid and volume is not None:
            volume = int((Decimal(volume) * (1 - scenario.depth_reduction)).to_integral_value(rounding=ROUND_FLOOR))
        return QuoteLevel(level.level, level.price * multiplier if level.price is not None else None, volume)
    return replace(snapshot, bids=tuple(transform(x, True) for x in snapshot.bids),
                   asks=tuple(transform(x) for x in snapshot.asks),
                   last_price=snapshot.last_price * multiplier if snapshot.last_price is not None else None,
                   reference_price=snapshot.reference_price * multiplier if snapshot.reference_price is not None else None,
                   capture_method=f"hypothetical:{scenario.name}", raw={})


def compare_scenario(snapshot, quantity, scenario, **kwargs):
    return {"baseline": simulate_execution(snapshot, quantity, **kwargs),
            "stressed": simulate_execution(apply_scenario(snapshot, scenario), quantity, **kwargs)}


def reverse_stress(snapshot, quantity, threshold=Decimal("0.7")):
    """Smallest 1 bp grid depth reduction with coverage STRICTLY below threshold."""
    threshold = rate(threshold, "threshold")
    def coverage(bp):
        return simulate_execution(apply_scenario(snapshot, Scenario(Decimal(bp) / 10000)), quantity).coverage_ratio
    if coverage(0) < threshold:
        return Decimal(0)
    if coverage(9000) >= threshold:
        return None
    lo, hi = 0, 9000
    while lo + 1 < hi:
        middle = (lo + hi) // 2
        if coverage(middle) < threshold:
            hi = middle
        else:
            lo = middle
    return Decimal(hi) / 10000
