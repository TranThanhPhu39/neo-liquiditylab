from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True)
class QuoteLevel:
    level: int
    price: Decimal | None
    volume: int | None

    @property
    def usable(self) -> bool:
        return (isinstance(self.price, Decimal) and self.price.is_finite() and self.price > 0
                and isinstance(self.volume, int) and not isinstance(self.volume,bool) and self.volume >= 0)


@dataclass(frozen=True)
class OrderBookSnapshot:
    symbol: str
    exchange: str
    provider: str
    upstream_source: str
    capture_method: str
    provider_version: str
    collected_at_utc: datetime
    source_timestamp: datetime | None
    market_session: str
    bids: tuple[QuoteLevel, ...]
    asks: tuple[QuoteLevel, ...]
    reference_price: Decimal | None = None
    last_price: Decimal | None = None
    issues: tuple[str, ...] = ()
    data_quality_status: str = "VALID"
    schema_version: str = "1.0"
    event_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self):
        if self.collected_at_utc.tzinfo is None or (self.source_timestamp is not None and self.source_timestamp.tzinfo is None):
            raise ValueError("Timestamp must be timezone-aware")

    @property
    def received_at_local(self) -> datetime:
        return self.collected_at_utc.astimezone(LOCAL_TZ)

    @property
    def observation_time(self) -> datetime:
        return self.source_timestamp or self.collected_at_utc

    def to_record(self) -> dict:
        record = {key: getattr(self, key) for key in (
            "symbol", "exchange", "provider", "upstream_source", "capture_method",
            "provider_version", "market_session", "data_quality_status", "schema_version", "event_id")}
        record.update(collected_at_utc=self.collected_at_utc.astimezone(timezone.utc).isoformat(),
                      received_at_local=self.received_at_local.isoformat(),
                      source_timestamp=self.source_timestamp.isoformat() if self.source_timestamp else None,
                      reference_price=str(self.reference_price) if self.reference_price is not None else None,
                      last_price=str(self.last_price) if self.last_price is not None else None,
                      issues=list(self.issues))
        for side, levels in (("bid", self.bids), ("ask", self.asks)):
            for level in levels:
                record[f"{side}_price_{level.level}"] = str(level.price) if level.price is not None else None
                record[f"{side}_vol_{level.level}"] = level.volume
        return record
