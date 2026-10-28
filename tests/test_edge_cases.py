from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import subprocess

import pytest

from src.data_pipeline.replay import latest_at
from src.data_providers.vnstock_provider import VnstockProvider
from src.data_providers.base import ProviderError
from src.finance.portfolio_liquidation import liquidate_portfolio
from src.models.portfolio import Position


def test_late_event_does_not_regress_current_view(book):
    newer = replace(book,source_timestamp=book.source_timestamp+timedelta(seconds=20),collected_at_utc=book.collected_at_utc+timedelta(seconds=20))
    late = replace(book,collected_at_utc=book.collected_at_utc+timedelta(seconds=40))
    assert latest_at([book,newer,late],late.collected_at_utc)["HPG"] is newer


def test_subprocess_timeout_is_sanitized(monkeypatch):
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired("secret not to log",1,output="secret")
    monkeypatch.setattr(subprocess,"run",timeout)
    with pytest.raises(ProviderError,match="TIMEOUT") as error:
        VnstockProvider()._request(["HPG"])
    assert "secret" not in str(error.value)


def test_mixed_provider_portfolio_rejected(book):
    other = replace(book,symbol="FPT",provider="vnstock")
    with pytest.raises(ValueError,match="khác nguồn"):
        liquidate_portfolio([Position("HPG",100),Position("FPT",100)],{"HPG":book,"FPT":other},now=book.observation_time)
