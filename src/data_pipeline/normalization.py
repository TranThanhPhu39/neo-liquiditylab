from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.models.orderbook import LOCAL_TZ, OrderBookSnapshot, QuoteLevel


def parse_timestamp(value) -> datetime | None:
    if value is None or str(value).strip() in ("", "None", "NaT", "nan"):
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp thiếu timezone; không suy đoán timezone nguồn")
    return result


def normalize(row: dict, *, provider: str, upstream_source: str, capture_method="rest_poll",
              provider_version="unknown", collected_at: datetime | None = None,
              price_unit="VND", volume_unit="shares") -> OrderBookSnapshot:
    if price_unit != "VND" or volume_unit != "shares":
        raise ValueError("Đơn vị chưa được xác minh: yêu cầu VND và shares")
    issues = list(row.get("issues", []))

    def number(key, volume=False):
        value = row.get(key)
        if value is None or str(value).strip().lower() in ("", "none", "nan", "<na>"):
            issues.append(f"MISSING:{key}")
            return None
        try:
            result = Decimal(str(value))
            if not result.is_finite() or result < 0 or (not volume and result == 0):
                raise ValueError()
            if volume and result != result.to_integral_value():
                raise ValueError()
            return int(result) if volume else result
        except (InvalidOperation, ValueError):
            issues.append(f"INVALID:{key}")
            return None

    timestamp = None
    try:
        timestamp = parse_timestamp(row.get("source_timestamp"))
    except (TypeError, ValueError):
        issues.append("INVALID:source_timestamp")
    collected = collected_at or parse_timestamp(row.get("collected_at_utc")) or datetime.now(timezone.utc)
    if collected.tzinfo is None:
        raise ValueError("collected_at must be timezone-aware")
    symbol = str(row.get("symbol", "")).strip().upper()
    if not symbol or not symbol.isalnum():
        raise ValueError("Mã cổ phiếu không hợp lệ")
    bids = tuple(QuoteLevel(i, number(f"bid_price_{i}"), number(f"bid_vol_{i}", True)) for i in range(1, 4))
    asks = tuple(QuoteLevel(i, number(f"ask_price_{i}"), number(f"ask_vol_{i}", True)) for i in range(1, 4))
    snapshot = OrderBookSnapshot(
        symbol=symbol, exchange=str(row.get("exchange", "UNKNOWN")), provider=provider,
        upstream_source=upstream_source, capture_method=capture_method, provider_version=provider_version,
        collected_at_utc=collected.astimezone(timezone.utc), source_timestamp=timestamp,
        market_session=str(row.get("market_session", "UNKNOWN")), bids=bids, asks=asks,
        reference_price=number("reference_price"), last_price=number("last_price"),
        issues=tuple(dict.fromkeys(issues)), event_id=row.get("event_id"), raw=dict(row),
        unit_verification_status=row.get("unit_verification_status","VERIFIED"),
        unit_contract_hash=row.get("unit_contract_hash"))
    from src.data_pipeline.validation import validate
    return validate(snapshot)
