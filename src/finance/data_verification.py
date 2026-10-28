"""Price arithmetic diagnostics; volume-based models require normalized data."""
from decimal import Decimal, InvalidOperation


def verify_raw_prices(storage):
    checks = []
    for batch in storage.raw_batches():
        for row in batch["payload"]["rows"]:
            check = {"raw_batch_id":batch["id"],"symbol":row.get("symbol"),
                     "received_at_utc":batch["received_at_utc"],"source_timestamp":None}
            try:
                bids = [Decimal(str(row.get(f"bid_price_{i}"))) for i in range(1,4)]
                asks = [Decimal(str(row.get(f"ask_price_{i}"))) for i in range(1,4)]
                if not all(x.is_finite() and x>0 for x in bids+asks):
                    raise ValueError()
                if any(a<b for a,b in zip(bids,bids[1:])) or any(a>b for a,b in zip(asks,asks[1:])) or bids[0]>=asks[0]:
                    raise ValueError()
                spread = asks[0]-bids[0]
                midpoint = (asks[0]+bids[0])/2
                check.update(status="PRICE_CHECK_PASSED",spread_source_price_units=str(spread),
                             relative_spread=str(spread/midpoint),relative_spread_bps=str(spread/midpoint*10000))
            except (InvalidOperation,ValueError,TypeError):
                check.update(status="PRICE_CHECK_FAILED",relative_spread=None)
            checks.append(check)
    return {"scope":"PRICE_ONLY_DIAGNOSTICS","observations_checked":len(checks),
            "price_checks_passed":sum(c["status"]=="PRICE_CHECK_PASSED" for c in checks),
            "price_checks_failed":sum(c["status"]=="PRICE_CHECK_FAILED" for c in checks),
            "volume_units_verified":False,"execution_stress_portfolio_on_raw":"BLOCKED_UNITS_UNVERIFIED",
            "checks":checks,"note":"Chỉ kiểm tra thứ tự và spread giá. Relative spread không cần đơn vị volume; không tính depth/coverage/VWAP/proceeds từ raw."}
