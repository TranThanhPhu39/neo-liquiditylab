from decimal import Decimal

from src.data_pipeline.validation import validate


def liquidity_metrics(snapshot):
    snapshot = validate(snapshot)
    if snapshot.data_quality_status == "INVALID":
        raise ValueError("Snapshot không hợp lệ: " + ", ".join(snapshot.issues))
    bid_depth = sum(x.volume for x in snapshot.bids if x.usable)
    ask_depth = sum(x.volume for x in snapshot.asks if x.usable)
    if snapshot.provider == 'dnse':
        bid_depth = bid_depth if any(x.usable for x in snapshot.bids) else None
        ask_depth = ask_depth if any(x.usable for x in snapshot.asks) else None
    total = bid_depth + ask_depth if bid_depth is not None and ask_depth is not None else None
    bid1, ask1 = snapshot.bids[0], snapshot.asks[0]
    spread = ask1.price - bid1.price if bid1.usable and ask1.usable else None
    mid = (ask1.price + bid1.price) / 2 if spread is not None else None
    return {"bid_depth": bid_depth, "ask_depth": ask_depth,
            "spread": spread, "mid_price": mid,
            "relative_spread": spread / mid if mid else None,
            "obi": Decimal(bid_depth - ask_depth) / total if total else None,
            "partial": snapshot.data_quality_status == "PARTIAL"}
