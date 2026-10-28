from dataclasses import replace
from datetime import timedelta
import sqlite3
import threading
import pytest

from src.data_pipeline.collector import collect_once
from src.data_pipeline.quality import quality_report
from src.data_pipeline.replay import latest_at, replay
from src.data_pipeline.storage import Storage
from src.data_providers.demo_provider import DemoProvider
from src.finance.historical_analytics import historical_frame, historical_summary


def test_insert_dedup_unchanged_filters(tmp_path, book):
    with Storage(tmp_path / "test.db") as db:
        assert db.insert(book)
        assert not db.insert(book)
        later = replace(book, collected_at_utc=book.collected_at_utc+timedelta(seconds=180))
        assert db.insert(later)
        assert len(db.query(symbol="HPG",provider="demo",quality="VALID")) == 2
        assert db.query(symbol="BAD") == []
        assert len(db.query(start=later.collected_at_utc)) == 1
        assert db.integrity_check() == "ok"
        report = quality_report(db)
        assert report["unchanged_ratio"] == 1 and report["real_observed_days"] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0] == 12


def test_raw_secret_allowlist(tmp_path, book):
    with Storage(tmp_path/"test.db") as db:
        db.insert(replace(book,raw={"token":"secret-value","nested":{"password":"secret-value"},"symbol":"HPG"}))
        raw = db.connection.execute("SELECT payload FROM raw_observations").fetchone()[0]
        assert "secret-value" not in raw and "HPG" in raw


def test_replay_no_lookahead_and_empty(tmp_path, book):
    with Storage(tmp_path/"test.db") as db:
        assert replay(db) == []
        assert historical_summary(historical_frame([]))["snapshots"] == 0
        late = replace(book, collected_at_utc=book.collected_at_utc+timedelta(minutes=5))
        db.insert(late)
        assert latest_at(replay(db), book.observation_time) == {}
        assert latest_at(replay(db), late.collected_at_utc)["HPG"].bids == book.bids
        assert historical_summary(historical_frame(replay(db)))["coverage_min"] == 1


def test_collector_runs(tmp_path):
    with Storage(tmp_path/"test.db") as db:
        r = collect_once(DemoProvider(),db,["HPG","FPT"])
        assert r["inserted"] == 2
        assert collect_once(DemoProvider(),db,["HPG","FPT"])["inserted"] == 0
        with pytest.raises(Exception):
            collect_once(DemoProvider(),db,["BAD"])
        assert quality_report(db)["failed_runs"] == 1


def test_temporary_database_lock(tmp_path, book):
    path = tmp_path/"lock.db"
    with Storage(path,timeout=.01) as db:
        lock = sqlite3.connect(path,check_same_thread=False)
        lock.execute("BEGIN IMMEDIATE")
        timer = threading.Timer(.05, lock.commit)
        timer.start()
        try:
            assert db.insert(book)
        finally:
            timer.join()
            lock.close()


def test_interrupt_rolls_back_before_backup(tmp_path):
    with Storage(tmp_path/"interrupt.db") as db:
        with pytest.raises(KeyboardInterrupt):
            with db.transaction():
                db.connection.execute("INSERT INTO collection_runs(started_at,provider,status) VALUES('test','demo','RUNNING')")
                raise KeyboardInterrupt()
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]==0
        db.backup_to(tmp_path/"safe_backup.db")
