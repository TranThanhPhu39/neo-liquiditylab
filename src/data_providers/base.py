from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    pass


class LocalRateLimit(ProviderError):
    """Local spacing guard; wait without making a network request."""


class NotConfigured(ProviderError):
    pass


class MarketDataProvider(ABC):
    @abstractmethod
    def get_snapshots(self, symbols):
        """Return normalized snapshots. Never fall back to synthetic data."""

    def get_snapshot(self, symbol):
        snapshots = self.get_snapshots([symbol])
        if not snapshots:
            raise ProviderError("NO DATA")
        return snapshots[0]

    @abstractmethod
    def get_provider_info(self):
        pass

    def health_check(self):
        """Local readiness only; must not spend quota as a side effect."""
        return {"ready": True, "network_verified": False, **self.get_provider_info()}
