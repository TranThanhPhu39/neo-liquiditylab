from src.data_providers.base import MarketDataProvider, NotConfigured, ProviderError


def create_provider(source, **kwargs):
    if source == "demo":
        from src.data_providers.demo_provider import DemoProvider
        return DemoProvider(**kwargs)
    if source == "vnstock":
        from src.data_providers.vnstock_provider import VnstockProvider
        return VnstockProvider(**kwargs)
    if source == "neo":
        from src.data_providers.neo_provider import NeoProvider
        return NeoProvider()
    raise ValueError(f"Nguồn dữ liệu không hợp lệ: {source}")
