import json
from pathlib import Path

from config.settings import ROOT
from src.data_pipeline.normalization import normalize
from src.data_providers.base import MarketDataProvider, ProviderError


class DemoProvider(MarketDataProvider):
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "data/sample/demo_quotes.json")

    def get_snapshots(self, symbols):
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        selected = [r for r in rows if r["symbol"] in symbols]
        missing = set(symbols) - {r["symbol"] for r in selected}
        if missing:
            raise ProviderError("Không có fixture DEMO cho: " + ", ".join(sorted(missing)))
        return [normalize(r, provider="demo", upstream_source="synthetic_fixture",
                          capture_method="fixture", provider_version="1") for r in selected]

    def get_provider_info(self):
        return {"provider": "demo", "label": "DEMO / SYNTHETIC DATA", "version": "1"}
