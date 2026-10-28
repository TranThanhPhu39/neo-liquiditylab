from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from src.finance.execution_simulator import simulate_execution, rate


def liquidate_portfolio(positions, snapshots, fraction=1, *, now=None, stale_seconds=600,
                        max_skew_seconds=60, fee_rate=0, sell_tax_rate=0, allow_stale=False):
    fraction = rate(fraction, "fraction")
    now = now or datetime.now(timezone.utc)
    if len({p.symbol for p in positions}) != len(positions):
        raise ValueError("Trùng mã danh mục")
    sources = {(s.provider, s.upstream_source) for s in snapshots.values()}
    if len(sources) > 1:
        raise ValueError("Không cộng danh mục có snapshot khác nguồn")
    rows, exclusions, results = [], {}, []
    requested_total = sum(int((Decimal(p.quantity) * fraction).to_integral_value(rounding=ROUND_FLOOR)) for p in positions)
    relevant = [snapshots[p.symbol] for p in positions if p.symbol in snapshots]
    anchor = max((s.observation_time for s in relevant), default=now)
    reference_value, reference_missing = Decimal(0), []
    for position in positions:
        quantity = int((Decimal(position.quantity) * fraction).to_integral_value(rounding=ROUND_FLOOR))
        if quantity == 0:
            continue
        snapshot = snapshots.get(position.symbol)
        reason = None
        if snapshot is None:
            reason = "NO DATA"
        elif abs((anchor - snapshot.observation_time).total_seconds()) > max_skew_seconds:
            reason = "UNSYNCHRONIZED"
        elif not allow_stale and (now - snapshot.observation_time).total_seconds() > stale_seconds:
            reason = "STALE DATA"
        elif (snapshot.observation_time - now).total_seconds() > 5:
            reason = "FUTURE DATA"
        if reason:
            exclusions[position.symbol] = {"reason": reason, "quantity": quantity}
            continue
        try:
            result = simulate_execution(snapshot, quantity, fee_rate=fee_rate, sell_tax_rate=sell_tax_rate)
        except ValueError as error:
            exclusions[position.symbol] = {"reason": str(error), "quantity": quantity}
            continue
        results.append(result)
        price = snapshot.last_price if snapshot.last_price is not None else snapshot.reference_price
        if price is None:
            reference_missing.append(position.symbol)
        else:
            reference_value += price * quantity
        rows.append({"symbol": position.symbol, "requested": quantity, "covered": result.executed_quantity,
                     "uncovered": result.uncovered_quantity, "coverage": float(result.coverage_ratio),
                     "gross_value": result.gross_value, "cost": result.book_walking_cost,
                     "net_value": result.net_value})
    analyzed = sum(r.requested_quantity for r in results)
    covered = sum(r.executed_quantity for r in results)
    return {"rows": rows, "exclusions": exclusions, "requested": requested_total,
            "analyzed_quantity": analyzed, "excluded_quantity": requested_total - analyzed,
            "covered": covered, "uncovered": analyzed - covered,
            "coverage": Decimal(covered) / analyzed if analyzed else None,
            "gross_value": sum((r.gross_value for r in results), Decimal(0)) if results else None,
            "net_value": sum((r.net_value for r in results), Decimal(0)) if results else None,
            "cost": sum((r.book_walking_cost or Decimal(0) for r in results), Decimal(0)) if results else None,
            "cost_undefined_symbols": [r.symbol for r in results if r.book_walking_cost is None],
            "reference_value": reference_value if results else None, "reference_missing": reference_missing}
