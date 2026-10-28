from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess

import pytest

from src.data_pipeline.raw_collection import collect_raw_once, raw_quality_report, replay_raw_at, request_raw
from src.data_pipeline.storage import Storage
from src.data_providers.base import NotConfigured, ProviderError
from scripts.collect_raw_market import capture_if_due
from src.data_pipeline.daily_report import build_daily_report


def payload(book, **kwargs):
    return {"version":"5.2.1","account":{"authenticated":True},"rows":[book.to_record()],
            "received_at_utc":book.collected_at_utc.isoformat(),"missing_columns":[],**kwargs}


def test_raw_capture_separate_from_finance_and_rate_gate(tmp_path,book):
    with Storage(tmp_path/"raw.db") as db:
        r = collect_raw_once(db,["HPG"],transport=lambda _:payload(book))
        assert r["status"]=="RAW_ONLY" and r["inserted_rows"]==1
        assert db.query()==[]
        assert db.raw_batches()[0]["units_status"]=="UNVERIFIED"
        with pytest.raises(ProviderError,match="LOCAL_RATE_LIMIT"):
            collect_raw_once(db,["HPG"],transport=lambda _:payload(book))
        report = raw_quality_report(db)
        assert report["symbol_observations"]==1 and not report["finance_enabled"]
        assert report["sessions_with_verified_source_time"]==0


def test_raw_secret_allowlist_backup_and_replay(tmp_path,book):
    with Storage(tmp_path/"raw.db") as db:
        response = payload(book,token="secret-value",rows=[dict(book.to_record(),token="secret-value")])
        run = db.start_run("vnstock",["HPG"])
        assert db.insert_raw_batch(response,run)
        assert not db.insert_raw_batch(response,run)
        later = payload(book,received_at_utc=(book.collected_at_utc+timedelta(seconds=180)).isoformat())
        assert db.insert_raw_batch(later,run)
        batches = db.raw_batches()
        assert "secret-value" not in json.dumps(batches)
        assert replay_raw_at(batches,book.collected_at_utc-timedelta(seconds=1))=={}
        assert replay_raw_at(batches,book.collected_at_utc)["HPG"]["received_at_utc"]==book.collected_at_utc.isoformat()
        path = tmp_path/"backup.db"
        db.backup_to(path)
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM raw_quote_batches").fetchone()[0]==2
            assert conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        with pytest.raises(FileExistsError):
            db.backup_to(path)
        with pytest.raises(ValueError):
            db.backup_to(tmp_path/"raw.db")


def test_raw_collection_requires_authenticated_account(tmp_path,book):
    with Storage(tmp_path/"raw.db") as db:
        with pytest.raises(NotConfigured):
            collect_raw_once(db,["HPG"],transport=lambda _:payload(book,account={"authenticated":False}))
        assert db.raw_batches()==[]
        assert raw_quality_report(db)["failed_runs"]==1


def test_raw_transport_sanitizes_errors():
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired("secret",1)
    with pytest.raises(ProviderError,match="TIMEOUT"):
        request_raw(["HPG"],runner=timeout)


def test_scheduler_waits_for_spacing_without_request(tmp_path):
    with Storage(tmp_path/"spacing.db") as db:
        db.start_run("vnstock",["HPG"])
        calls = []
        result = capture_if_due(db,["HPG"],capture=lambda *args:calls.append("network"))
        assert result["status"]=="WAITING_LOCAL_RATE_LIMIT"
        assert 0<result["wait_seconds"]<=180.1
        assert calls==[]
        future = datetime.now(timezone.utc)+timedelta(seconds=181)
        assert db.collection_wait_seconds("vnstock",now=future)==0


def test_raw_dashboard_does_not_produce_financial_metrics(tmp_path,monkeypatch,book):
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    path = tmp_path/"ui.db"
    with Storage(path) as db:
        collect_raw_once(db,["HPG"],transport=lambda _:payload(book))
    monkeypatch.setenv("DATABASE_PATH",str(path))
    monkeypatch.setenv("DATA_SOURCE","vnstock")
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"src/app/main.py"),default_timeout=30).run()
    assert not at.exception and not at.metric
    assert any("RAW ONLY" in x.value for x in at.warning)
    at.sidebar.radio[1].set_value("Stress danh mục").run()
    assert not at.exception and not at.metric


def test_report_describes_real_rows_without_assuming_volume(tmp_path,book):
    with Storage(tmp_path/"report.db") as db:
        collect_raw_once(db,["HPG"],transport=lambda _:payload(book))
        result = build_daily_report(db,tmp_path/"daily",{"collector_state":"STOPPED"})
        from pathlib import Path
        report = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
        assert report["quality"]["symbol_observations"]==1
        assert report["per_symbol"][0]["observations"]==1
        assert report["execution_stress_portfolio_status"]=="BLOCKED_UNITS_UNVERIFIED"
        assert "RAW ONLY" in Path(result["markdown"]).read_text(encoding="utf-8")
        assert "plotly" in Path(result["html"]).read_text(encoding="utf-8")


def test_unchanged_batch_ignores_provider_row_order(tmp_path,book):
    with Storage(tmp_path/"order.db") as db:
        first = payload(book,rows=[dict(book.to_record(),symbol="HPG"),dict(book.to_record(),symbol="FPT")])
        run_id = db.start_run("vnstock",["HPG","FPT"])
        db.insert_raw_batch(first,run_id)
        second = dict(first,rows=list(reversed(first["rows"])),received_at_utc=(book.collected_at_utc+timedelta(seconds=180)).isoformat())
        db.insert_raw_batch(second,run_id)
        assert raw_quality_report(db)["unchanged_batch_ratio"]==1
