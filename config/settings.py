import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def get_vnstock_api_key(environ=None):
    """Accept the project's canonical name and the user's local alias."""
    environ = os.environ if environ is None else environ
    return environ.get("VNSTOCK_API_KEY") or environ.get("vnstock_api")


@dataclass(frozen=True)
class Settings:
    data_source: str = field(default_factory=lambda: os.getenv("DATA_SOURCE", "demo"))
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", str(ROOT / "data/liquidity.db")))
    interval: int = field(default_factory=lambda: int(os.getenv("COLLECTION_INTERVAL_SECONDS", "180")))
    stale_seconds: int = field(default_factory=lambda: int(os.getenv("STALE_AFTER_SECONDS", "600")))
    max_skew_seconds: int = field(default_factory=lambda: int(os.getenv("MAX_SNAPSHOT_SKEW_SECONDS", "60")))

