import os

from src.data_providers.base import MarketDataProvider, NotConfigured


class NeoProvider(MarketDataProvider):
    """Integration boundary; transport and mapping intentionally unverified."""
    def get_provider_info(self):
        return {"provider": "neo", "label": "NEO API — NOT CONFIGURED", "version": "unverified"}

    def health_check(self):
        return {"ready": False, "network_verified": False,
                "credentials_present": bool(os.getenv("NEO_TOKEN")), **self.get_provider_info()}

    def get_snapshots(self, symbols):
        raise NotConfigured("NEO chưa cấu hình/kiểm chứng transport và mapping. Không có kết nối NEO thật.")
