from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import pytest

from src.models.portfolio import Position, parse_portfolio
from src.data_providers.demo_provider import DemoProvider
from src.finance.portfolio_liquidation import liquidate_portfolio


def test_portfolio_and_missing(book):
    positions = [Position("HPG",4000),Position("BAD",100)]
    r = liquidate_portfolio(positions,{"HPG":book},now=book.observation_time)
    assert r["requested"] == 4100
    assert r["analyzed_quantity"] == 4000
    assert r["coverage"] == Decimal(".875")
    assert r["uncovered"] == 500 and r["excluded_quantity"] == 100
    assert r["gross_value"] == 104825000


def test_stale_and_skew(book):
    assert liquidate_portfolio([Position("HPG",100)],{"HPG":book},now=book.observation_time+timedelta(days=1))["gross_value"] is None
    s = DemoProvider().get_snapshot("FPT")
    s = replace(s,source_timestamp=s.source_timestamp+timedelta(minutes=2),collected_at_utc=s.collected_at_utc+timedelta(minutes=2))
    r = liquidate_portfolio([Position("HPG",100),Position("FPT",100)],{"HPG":book,"FPT":s},now=s.observation_time)
    assert r["exclusions"]["HPG"]["reason"] == "UNSYNCHRONIZED"


def test_csv_and_fraction(book):
    p = parse_portfolio("symbol,quantity\nHPG,4000\n")
    assert liquidate_portfolio(p,{"HPG":book},fraction=".5",now=book.observation_time)["covered"] == 2000
    for csv in ("symbol,quantity\nHPG,-1", "symbol,quantity\nHPG,2.5", "symbol,quantity\nHPG,2\nHPG,3", "x,y\na,b", "symbol,quantity\nHPG,NaN"):
        with pytest.raises((ValueError, ArithmeticError)):
            parse_portfolio(csv)
