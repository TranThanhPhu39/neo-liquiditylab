from datetime import datetime, timezone
from decimal import Decimal
import pytest

from src.data_pipeline.normalization import normalize, parse_timestamp
from src.data_pipeline.validation import freshness, scheduled_session


def normal(row):
    return normalize(row, provider="test", upstream_source="fixture")


def test_units_timezone(book):
    b = normal(book.to_record())
    assert b.bids[0].price == Decimal(30000)
    assert b.received_at_local.hour == 10
    with pytest.raises(ValueError):
        normalize(book.to_record(), provider="test", upstream_source="fixture", volume_unit="lots")
    with pytest.raises(ValueError):
        parse_timestamp("2026-10-20T10:00:00")
    assert freshness(b, datetime(2026,10,21,tzinfo=timezone.utc)) == "STALE DATA"


@pytest.mark.parametrize("value", ["ATO", "ATC", -1, 0, "x", "Infinity", "NaN", None])
def test_bad_price(book, value):
    r = book.to_record()
    r["bid_price_1"] = value
    b = normal(r)
    assert b.bids[0].price is None
    assert b.data_quality_status == "PARTIAL"


@pytest.mark.parametrize("value", [-1, "x", 1.2, None])
def test_bad_volume(book, value):
    r = book.to_record()
    r["bid_vol_1"] = value
    assert normal(r).bids[0].volume is None


@pytest.mark.parametrize("field,value,issue", [("bid_price_2",31000,"UNSORTED:bid"),("ask_price_1",29900,"CROSSED"),("ask_price_1",30000,"LOCKED"),("source_timestamp","not-time","INVALID:source_timestamp")])
def test_invalid_book(book, field, value, issue):
    row = book.to_record()
    row[field] = value
    s = normal(row)
    assert s.data_quality_status == "INVALID"
    assert issue in s.issues


def test_schedule():
    assert scheduled_session(parse_timestamp("2026-10-20T10:00:00+07:00")) == "SCHEDULED_CONTINUOUS"
    assert scheduled_session(parse_timestamp("2026-10-20T12:00:00+07:00")) == "OUTSIDE_COLLECTION_WINDOW"
    assert scheduled_session(parse_timestamp("2026-10-24T10:00:00+07:00")) == "CLOSED"
