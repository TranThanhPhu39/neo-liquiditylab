from dataclasses import replace
from datetime import datetime, time

from src.models.orderbook import LOCAL_TZ, OrderBookSnapshot


def scheduled_session(at: datetime, holidays=()) -> str:
    """Conservative polling window, NOT an authoritative exchange calendar."""
    local = at.astimezone(LOCAL_TZ)
    if local.weekday() >= 5 or local.date().isoformat() in holidays:
        return "CLOSED"
    current = local.time().replace(tzinfo=None)
    if time(9, 15) <= current < time(11, 30) or time(13) <= current < time(14, 30):
        return "SCHEDULED_CONTINUOUS"
    return "OUTSIDE_COLLECTION_WINDOW"


def validate(snapshot: OrderBookSnapshot) -> OrderBookSnapshot:
    issues = list(snapshot.issues)
    severe = False
    for side, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
        if len(levels) != 3 or [x.level for x in levels] != [1, 2, 3]:
            issues.append(f"INVALID_LEVELS:{side}")
            severe = True
        if any(not x.usable for x in levels):
            issues.append(f"PARTIAL:{side}")
        prices = [x.price for x in levels if x.usable]
        if any((a < b if side == "bid" else a > b) for a, b in zip(prices, prices[1:])):
            issues.append(f"UNSORTED:{side}")
            severe = True
    bid1 = snapshot.bids[0] if snapshot.bids else None
    ask1 = snapshot.asks[0] if snapshot.asks else None
    if bid1 and ask1 and bid1.usable and ask1.usable and bid1.price >= ask1.price:
        issues.append("CROSSED" if bid1.price > ask1.price else "LOCKED")
        severe = True
    if "INVALID:source_timestamp" in issues:
        severe = True
    if snapshot.source_timestamp and (snapshot.source_timestamp - snapshot.collected_at_utc).total_seconds() > 5:
        issues.append("FUTURE_SOURCE_TIMESTAMP")
        severe = True
    status = "INVALID" if severe else "PARTIAL" if issues else "VALID"
    return replace(snapshot, issues=tuple(dict.fromkeys(issues)), data_quality_status=status)


def freshness(snapshot, now: datetime, stale_seconds=600) -> str:
    if (now - snapshot.observation_time).total_seconds() > stale_seconds:
        return "STALE DATA"
    return "SOURCE TIME UNKNOWN" if snapshot.source_timestamp is None else "RECENT SNAPSHOT"
