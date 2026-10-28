"""Run historical A1/B5 checks on normalized Vnstock snapshots, without network."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

from config.settings import Settings
from src.data_pipeline.replay import latest_at
from src.data_pipeline.storage import Storage
from src.finance.execution_simulator import simulate_execution
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.portfolio_liquidation import liquidate_portfolio
from src.finance.stress_testing import compare_scenario
from src.models.portfolio import Position
from src.models.scenario import Scenario


def validate_sample(storage):
    books = [s for s in storage.query(provider="vnstock") if s.capture_method == "raw_migration"]
    if not books:
        raise ValueError("No normalized Vnstock observations; never use raw as shares")
    checks = []
    for book in books:
        metrics = liquidity_metrics(book)
        for side in ("sell", "buy"):
            result = simulate_execution(book, 5000, side=side, fee_rate="0.0015", sell_tax_rate="0.001")
            levels = book.bids if side == "sell" else book.asks
            remaining, gross = 5000, Decimal(0)
            for level in levels:
                if level.usable:
                    fill = min(remaining, level.volume)
                    gross += fill * level.price
                    remaining -= fill
            if result.gross_value != gross or result.uncovered_quantity != remaining:
                raise ValueError("Independent book-walk arithmetic differs")
            if result.executed_quantity > 5000 or result.uncovered_quantity < 0:
                raise ValueError("Execution invariant failed")
            if side == "buy" and result.sell_tax != 0:
                raise ValueError("Buy unexpectedly incurs sell tax")
        quantity = metrics["bid_depth"] + 100
        comparison = compare_scenario(book, quantity, Scenario("0.5"))
        baseline, stressed = comparison["baseline"], comparison["stressed"]
        expected_stress_depth = sum(level.volume // 2 for level in book.bids if level.usable)
        if (baseline.executed_quantity != metrics["bid_depth"] or baseline.uncovered_quantity != 100
                or stressed.executed_quantity != expected_stress_depth
                or stressed.executed_quantity > baseline.executed_quantity):
            raise ValueError("Partial-depth or 50% stress invariant failed")
        checks.append({"symbol":book.symbol,"received_at_local":book.received_at_local.isoformat(),
            "metrics":metrics,"quantity_for_partial_sell":quantity,
            "partial_sell":asdict(baseline),"stress_50_percent":asdict(stressed),"status":"PASSED"})
    anchor = max(s.collected_at_utc for s in books)
    latest = latest_at(books, anchor)
    portfolio = liquidate_portfolio([Position(symbol, 5000) for symbol in sorted(latest)], latest, now=anchor,
                                    fee_rate="0.0015", sell_tax_rate="0.001")
    if portfolio["exclusions"] or portfolio["analyzed_quantity"] != 5000 * len(latest):
        raise ValueError("Historical portfolio unexpectedly excludes an observation")
    return {"status":"PASSED", "scope":"HISTORICAL_NORMALIZED_SAMPLE_WITH_HYPOTHETICAL_ORDERS",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(), "normalized_observations":len(books),
        "execution_cases_checked":len(books)*2,"stress_cases_checked":len(books),
        "source_timestamp_verified":False,"analysis_anchor_utc":anchor.isoformat(),
        "portfolio_positions":"SIMULATED: 5000 shares per symbol, not broker holdings",
        "fee_rate_assumption":"0.0015","sell_tax_rate_assumption":"0.001",
        "portfolio":portfolio,"checks":checks,
        "limitations":"Receipt-time replay only. Three visible levels; uncovered quantity has no assigned execution value. No live execution or NEO connection verified."}


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--report",default="report/normalized_validation_2026-10-28.json")
    args = parser.parse_args()
    with Storage(args.db) as db:
        report = validate_sample(db)
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({key:report[key] for key in ("status","normalized_observations","execution_cases_checked","stress_cases_checked")}))


if __name__=="__main__":
    main()
