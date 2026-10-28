from datetime import timedelta
from dataclasses import replace
import json
import subprocess
import sys

import pytest

from src.data_pipeline.collector import collect_once
from src.data_pipeline.quality import quality_report
from src.data_pipeline.storage import Storage
from src.data_providers.base import ProviderError
from src.data_providers.demo_provider import DemoProvider
from src.data_providers.vnstock_provider import VnstockProvider
from src.finance.historical_analytics import historical_frame


def test_run_rate_gate_across_provider_instances(tmp_path,book):
    response = {"rows":[dict(book.to_record(),close_price=30000)],"version":"5.2.1"}
    with Storage(tmp_path/"rate.db") as db:
        one = VnstockProvider(access_confirmed=True,volume_unit="shares",transport=lambda _:response)
        collect_once(one,db,["HPG"])
        two = VnstockProvider(access_confirmed=True,volume_unit="shares",transport=lambda _:response)
        with pytest.raises(ProviderError,match="LOCAL_RATE_LIMIT"):
            collect_once(two,db,["HPG"])


def test_timeout_retry_and_failed_audit(tmp_path,book):
    class Flaky(DemoProvider):
        count = 0
        def get_snapshots(self,symbols):
            self.count += 1
            if self.count == 1:
                raise TimeoutError()
            return [book]
    sleeps = []
    with Storage(tmp_path/"retry.db") as db:
        assert collect_once(Flaky(),db,["HPG"],retries=1,sleep=sleeps.append)["inserted"] == 1
        assert sleeps == [1]


def test_late_and_gap_candidates(tmp_path,book):
    with Storage(tmp_path/"quality.db") as db:
        db.insert(book)
        db.insert(replace(book,collected_at_utc=book.collected_at_utc+timedelta(minutes=10),source_timestamp=book.source_timestamp-timedelta(minutes=1)))
        r = quality_report(db)
        assert r["late_observations"] == 1
        assert r["gap_candidates"][0]["seconds"] == 600


def test_invalid_history_has_safe_chart_columns(book):
    invalid = replace(book,bids=tuple(reversed(book.bids)))
    frame = historical_frame([invalid])
    assert "relative_spread" in frame and frame["coverage"].isna().all()


def test_cli_utf8_and_export(tmp_path):
    dbpath = str(tmp_path/"cli.db")
    base = [sys.executable,"-m","src.cli"]
    once = subprocess.run(base+["once","--source","demo","--db",dbpath],capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert once.returncode == 0 and json.loads(once.stdout)["inserted"] == 3
    reportpath = tmp_path/"quality.json"
    quality = subprocess.run(base+["validate","--source","demo","--db",dbpath,"--output",str(reportpath)],capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert quality.returncode == 0
    assert json.loads(reportpath.read_text(encoding="utf-8"))["snapshots"] == 3
