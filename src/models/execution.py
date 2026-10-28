from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Fill:
    level: int
    price: Decimal
    quantity: int


@dataclass(frozen=True)
class ExecutionResult:
    symbol: str
    side: str
    requested_quantity: int
    executed_quantity: int
    uncovered_quantity: int
    coverage_ratio: Decimal
    gross_value: Decimal
    vwap: Decimal | None
    benchmark_value: Decimal | None
    book_walking_cost: Decimal | None
    cost_bps: Decimal | None
    fees: Decimal
    sell_tax: Decimal
    net_value: Decimal
    fills: tuple[Fill, ...]
    benchmark: str
    warnings: tuple[str, ...]
