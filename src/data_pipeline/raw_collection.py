"""Authenticated raw capture; unverified units cannot reach financial engines."""
from datetime import datetime, timezone
import json
import subprocess
import sys

from config.settings import ROOT
from src.data_providers.base import NotConfigured, ProviderError
from src.models.orderbook import LOCAL_TZ


def request_raw(symbols, runner=subprocess.run):
    try:
        result = runner([sys.executable,"-m","src.data_providers.vnstock_worker"],
            input=json.dumps({"operation":"capture_raw","symbols":list(symbols)}),
            capture_output=True,text=True,timeout=45,cwd=ROOT)
        response = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise ProviderError("TIMEOUT: raw request đã được dừng") from None
    except (ValueError,TypeError):
        raise ProviderError("INVALID_WORKER_RESPONSE") from None
    if result.returncode:
        raise ProviderError("RAW_WORKER_FAILED")
    if response.get("error") in ("AUTHENTICATION_REQUIRED","QUOTA_UNAVAILABLE"):
        raise NotConfigured(response["error"])
    if response.get("error") or not response.get("market_request_succeeded"):
        # Do not echo arbitrary external text.
        raise ProviderError("RAW_SOURCE_ERROR")
    if response.get("version") != "5.2.1" or not response.get("account",{}).get("authenticated"):
        raise NotConfigured("Phiên bản hoặc đăng nhập raw chưa được xác minh")
    if not response.get("rows"):
        raise ProviderError("NO DATA")
    return response


def collect_raw_once(storage, symbols, transport=request_raw, *, min_interval=180):
    # Whole-market scheduler spaces disjoint batches; default single-list cadence stays 180s.
    if min_interval < 5:
        raise ValueError("Minimum batch spacing is 5 seconds")
    run_id = storage.start_run("vnstock",symbols,min_interval=min_interval)
    try:
        response = transport(symbols)
        if response.get("version") != "5.2.1" or response.get("account",{}).get("authenticated") is not True:
            raise NotConfigured("Raw collection requires verified SDK and authenticated access")
        rows = response.get("rows",[])
        returned = [row.get("symbol") for row in rows]
        if not rows or len(returned) != len(set(returned)) or not set(returned) <= set(symbols):
            raise ProviderError("INVALID_RAW_SYMBOLS")
        inserted = storage.insert_raw_batch(response,run_id)
        missing = sorted(set(symbols)-set(returned))
        status = "PARTIAL_RAW" if missing or response.get("missing_columns") else "RAW_ONLY"
        storage.finish_run(run_id,status,len(rows) if inserted else 0,"UNITS_UNVERIFIED")
        return {"run_id":run_id,"status":status,"inserted_rows":len(rows) if inserted else 0,
                "received_at_utc":response["received_at_utc"],"missing_symbols":missing,"units_verified":False}
    except BaseException as error:
        storage.finish_run(run_id,"FAILED",error=type(error).__name__)
        raise


def raw_quality_report(storage, expected_interval=180):
    batches = storage.raw_batches()
    rows = [row for batch in batches for row in batch["payload"]["rows"]]
    times = [datetime.fromisoformat(batch["received_at_utc"]) for batch in batches]
    pairs = len(batches)-1
    # Provider row ordering is not a market event.
    books = [{row["symbol"]:row for row in b["payload"]["rows"]} for b in batches]
    unchanged = sum(a==b for a,b in zip(books,books[1:]))
    gaps = [{"from":a.isoformat(),"to":b.isoformat(),"seconds":(b-a).total_seconds()}
            for a,b in zip(times,times[1:]) if a.astimezone(LOCAL_TZ).date()==b.astimezone(LOCAL_TZ).date()
            and (a.astimezone(LOCAL_TZ).hour<12)==(b.astimezone(LOCAL_TZ).hour<12)
            and (b-a).total_seconds()>expected_interval*1.5]
    return {"provider":"vnstock","upstream_source":"kbs","storage_mode":"RAW_ONLY",
        "units_verified":False,"finance_enabled":False,"batches":len(batches),"symbol_observations":len(rows),
        "symbols":sorted({row.get("symbol") for row in rows}),
        "collection_days":len({t.astimezone(LOCAL_TZ).date() for t in times}),
        "sessions_with_verified_source_time":0,"source_timestamp_missing_ratio":1 if rows else None,
        "batches_missing_columns":sum(bool(b["payload"]["missing_columns"]) for b in batches),
        "unchanged_batch_ratio":unchanged/pairs if pairs>0 else None,"gap_candidates":gaps,
        "first_received_local":times[0].astimezone(LOCAL_TZ).isoformat() if times else None,
        "latest_received_local":times[-1].astimezone(LOCAL_TZ).isoformat() if times else None,
        "normalized_snapshot_count":len(storage.query(provider="vnstock")),
        "failed_runs":storage.connection.execute("SELECT COUNT(*) FROM collection_runs WHERE provider='vnstock' AND status='FAILED'").fetchone()[0],
        "integrity_check":storage.integrity_check(),
        "note":"Dữ liệu nguồn thật, chỉ lưu raw. Chưa chứng minh đơn vị CP hoặc timestamp thị trường; chưa chạy A1/B5 trên raw."}


def replay_raw_at(batches, at):
    if at.tzinfo is None:
        raise ValueError("Raw replay time requires timezone")
    latest = {}
    for batch in batches:
        if datetime.fromisoformat(batch["received_at_utc"])<=at:
            for row in batch["payload"]["rows"]:
                latest[row["symbol"]] = {**row,"received_at_utc":batch["received_at_utc"],"units_status":"UNVERIFIED"}
    return latest
