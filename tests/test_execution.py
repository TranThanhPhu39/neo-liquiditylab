from dataclasses import replace
from decimal import Decimal
import pytest

from src.finance.execution_simulator import simulate_execution
from src.finance.liquidity_metrics import liquidity_metrics
from src.models.orderbook import QuoteLevel


def test_required_example(book):
    result = simulate_execution(book, 3000)
    assert [f.quantity for f in result.fills] == [1000, 1500, 500]
    assert result.gross_value == Decimal(89875000)
    assert result.vwap == Decimal(89875000) / 3000
    assert result.benchmark_value == 90000000
    assert result.book_walking_cost == 125000
    assert result.uncovered_quantity == 0


def test_required_partial(book):
    result = simulate_execution(book, 4000)
    assert result.executed_quantity == 3500
    assert result.uncovered_quantity == 500
    assert result.coverage_ratio == Decimal("0.875")
    assert result.gross_value == 104825000


@pytest.mark.parametrize("quantity", [1, 500, 1000, 3000, 3500, 4000, 100000])
def test_invariants(book, quantity):
    r = simulate_execution(book, quantity)
    assert r.executed_quantity <= min(quantity, 3500)
    assert r.executed_quantity + r.uncovered_quantity == quantity
    assert r.gross_value == sum(f.price * f.quantity for f in r.fills)
    assert r.book_walking_cost >= 0


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "3000"])
def test_invalid_quantity(book, quantity):
    with pytest.raises(ValueError):
        simulate_execution(book, quantity)


def test_small_exact_buy_fees(book):
    assert simulate_execution(book, 1000).vwap == 30000
    assert simulate_execution(book, 3500).uncovered_quantity == 0
    sell = simulate_execution(book, 3000, fee_rate="0.001", sell_tax_rate="0.002")
    assert sell.fees == Decimal(89875)
    assert sell.sell_tax == Decimal(179750)
    assert sell.net_value == Decimal(89605375)
    buy = simulate_execution(book, 100, side="buy", fee_rate="0.001", sell_tax_rate="0.5")
    assert buy.vwap == 30050
    assert buy.sell_tax == 0
    assert buy.net_value == Decimal(3008005)


def test_no_bids_and_missing_benchmark(book):
    empty = replace(book, bids=tuple(QuoteLevel(i, None, 0) for i in range(1,4)))
    r = simulate_execution(empty, 100)
    assert r.vwap is None and r.cost_bps is None and r.book_walking_cost is None
    assert r.executed_quantity == 0
    partial = replace(book, bids=(QuoteLevel(1, None, None), *book.bids[1:]))
    r = simulate_execution(partial, 500)
    assert r.gross_value == 29950 * 500
    assert r.book_walking_cost is None


def test_metrics(book):
    m = liquidity_metrics(book)
    assert m["bid_depth"] == 3500 and m["ask_depth"] == 4000
    assert m["spread"] == 50
    assert m["relative_spread"] == Decimal(50) / Decimal(30025)
    assert m["obi"] == Decimal(-500) / 7500
    empty = replace(book, bids=tuple(QuoteLevel(i, None, 0) for i in range(1,4)), asks=tuple(QuoteLevel(i, None, 0) for i in range(1,4)))
    assert liquidity_metrics(empty)["obi"] is None


def test_mid_benchmark(book):
    assert simulate_execution(book, 1000, benchmark="mid").book_walking_cost == 25000
