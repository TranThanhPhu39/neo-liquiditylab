import pytest

from src.data_providers import create_provider
from src.data_providers.base import NotConfigured, ProviderError
from src.data_providers.vnstock_provider import VnstockProvider


def test_not_configured():
    with pytest.raises(NotConfigured):
        create_provider("neo").get_snapshot("HPG")
    with pytest.raises(NotConfigured):
        VnstockProvider(access_confirmed=False).get_snapshot("HPG")
    with pytest.raises(NotConfigured):
        VnstockProvider(access_confirmed=True,volume_unit="lots").get_snapshot("HPG")


def test_vnstock_fixture_mapping(book):
    row = book.to_record()
    row.pop("source_timestamp")
    row["close_price"] = 30000
    provider = VnstockProvider(access_confirmed=True,volume_unit="shares",transport=lambda _: {"rows":[row],"version":"5.2.1"})
    result = provider.get_snapshot("HPG")
    assert result.provider == "vnstock" and result.last_price == 30000
    assert result.source_timestamp is None
    with pytest.raises(ProviderError,match="LOCAL_RATE_LIMIT"):
        provider.get_snapshot("HPG")


@pytest.mark.parametrize("payload", [{"rows":[],"version":"5.2.1"},{"rows":[],"version":"3.4"}])
def test_bad_payload(payload):
    p = VnstockProvider(access_confirmed=True,volume_unit="shares",transport=lambda _:payload)
    with pytest.raises(ProviderError):
        p.get_snapshot("HPG")


def test_timeout_no_fallback():
    def fail(_):
        raise TimeoutError("test timeout")
    p = VnstockProvider(access_confirmed=True,volume_unit="shares",transport=fail)
    with pytest.raises(TimeoutError):
        p.get_snapshot("HPG")
