from dataclasses import dataclass
from decimal import Decimal

from src.finance.execution_simulator import rate


@dataclass(frozen=True)
class Scenario:
    depth_reduction: Decimal = Decimal(0)
    price_shock: Decimal = Decimal(0)
    name: str = "Giả định"

    def __post_init__(self):
        object.__setattr__(self, "depth_reduction", rate(self.depth_reduction, "depth_reduction", maximum=Decimal("0.9")))
        object.__setattr__(self, "price_shock", rate(self.price_shock, "price_shock", Decimal("-0.99"), Decimal(1)))
