from decimal import Decimal
import pytest

from src.finance.stress_testing import apply_scenario, compare_scenario, reverse_stress
from src.models.scenario import Scenario


@pytest.mark.parametrize("shock", ["0", "0.1", "0.4", "0.5", "0.9"])
def test_depth_monotonic(book, shock):
    s = apply_scenario(book, Scenario(shock))
    pair = compare_scenario(book, 4000, Scenario(shock))
    assert pair["stressed"].executed_quantity <= pair["baseline"].executed_quantity
    assert s.asks == book.asks
    assert sum(x.volume for x in s.bids) == int(Decimal(3500) * (1-Decimal(shock)))


def test_price_shock(book):
    s = apply_scenario(book, Scenario("0.5", "-0.1"))
    assert s.bids[0].price == 27000
    assert s.bids[0].volume == 500
    assert book.bids[0].volume == 1000


def test_reverse(book):
    shock = reverse_stress(book, 4000, "0.7")
    assert shock is not None
    assert compare_scenario(book,4000,Scenario(shock))["stressed"].coverage_ratio < Decimal(".7")
    assert compare_scenario(book,4000,Scenario(shock-Decimal(".0001")))["stressed"].coverage_ratio >= Decimal(".7")
    assert reverse_stress(book,4000,".9") == 0
    assert reverse_stress(book,1,".1") is None


@pytest.mark.parametrize("shock", [-.1, 1, "nan"])
def test_invalid_scenario(shock):
    with pytest.raises(ValueError):
        Scenario(shock)
