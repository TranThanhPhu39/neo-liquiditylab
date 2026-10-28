import io
import json
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import get_vnstock_api_key
from scripts.probe_vnstock import probe
from src.data_providers.diagnostics import account_summary, error_summary, quote_evidence
from src.data_providers import vnstock_worker


def test_key_alias_and_canonical_precedence():
    assert get_vnstock_api_key({"vnstock_api":"alias-test"}) == "alias-test"
    assert get_vnstock_api_key({"VNSTOCK_API_KEY":"canonical-test","vnstock_api":"alias-test"}) == "canonical-test"
    assert get_vnstock_api_key({"VNSTOCK_API_KEY":"","vnstock_api":"alias-test"}) == "alias-test"
    assert get_vnstock_api_key({}) is None


def test_diagnostics_exclude_sensitive_fields():
    account = account_summary({"tier":"free","authenticated":True,"token":"private-value","device_id":"private-value"},
        {"limits":{"per_day":300,"api_key":"private-value"},"used":{"day":1,"token":"private-value"}})
    assert "private-value" not in json.dumps(account)
    class ErrorWithSecrets(Exception):
        code = "INVALID code with private-value"
        retryable = False
    error = error_summary(ErrorWithSecrets("private-value"))
    assert "private-value" not in json.dumps(error)
    frame = pd.DataFrame([{"symbol":"FPT","bid_vol_1":100,"token":"private-value"}])
    frame.attrs.update(source="kbs",token="private-value")
    evidence = quote_evidence(frame)
    assert "private-value" not in json.dumps(evidence)
    assert evidence["missing_columns"] and evidence["rows"][0]["bid_vol_1"] == 100


def test_probe_timeout_no_raw_logs():
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired("private-value",1,output="private-value")
    report = probe(quote=True,runner=timeout)
    assert report["error"] == "TIMEOUT"
    assert "private-value" not in json.dumps(report)


def test_worker_authentication_with_alias_offline(monkeypatch,capsys):
    calls = []
    fake = SimpleNamespace(login=lambda key:calls.append(key),
        status=lambda:{"tier":"free","authenticated":True}, usage=lambda:{"limits":{"per_day":1000},"used":{"day":0}},
        Market=lambda:SimpleNamespace())
    monkeypatch.setitem(sys.modules,"vnstock",fake)
    monkeypatch.setattr(vnstock_worker.importlib.metadata,"version",lambda _:"5.2.1")
    monkeypatch.delenv("VNSTOCK_API_KEY",raising=False)
    monkeypatch.setenv("vnstock_api","alias-unit-test-only")
    monkeypatch.setattr(sys,"stdin",io.StringIO(json.dumps({"operation":"probe","quote":False})))
    vnstock_worker.main()
    result = json.loads(capsys.readouterr().out)
    assert result["account"]["authenticated"] is True
    assert result["market_request_attempted"] is False
    assert calls == ["alias-unit-test-only"]


@pytest.mark.parametrize("operation",[None,"capture_raw"])
def test_worker_blocks_guest_collection_without_quote(monkeypatch,capsys,operation):
    fake = SimpleNamespace(status=lambda:{"authenticated":False},usage=lambda:{},Market=lambda:None)
    monkeypatch.setitem(sys.modules,"vnstock",fake)
    monkeypatch.setattr(vnstock_worker.importlib.metadata,"version",lambda _:"5.2.1")
    monkeypatch.setattr(vnstock_worker,"get_vnstock_api_key",lambda:None)
    monkeypatch.setattr(sys,"stdin",io.StringIO(json.dumps({"symbols":["FPT"],"operation":operation})))
    vnstock_worker.main()
    assert json.loads(capsys.readouterr().out)["error"] == "AUTHENTICATION_REQUIRED"


def test_worker_raw_quota_guard_prevents_request(monkeypatch,capsys):
    calls = []
    fake = SimpleNamespace(status=lambda:{"tier":"free","authenticated":True},
        usage=lambda:{"limits":{"per_minute":1,"per_hour":10,"per_day":20,"per_month":30},
                      "used":{"minute":1,"hour":1,"day":1,"month":1}},
        Market=lambda:SimpleNamespace(quote=lambda *args,**kwargs:calls.append("quote")))
    monkeypatch.setitem(sys.modules,"vnstock",fake)
    monkeypatch.setattr(vnstock_worker.importlib.metadata,"version",lambda _:"5.2.1")
    monkeypatch.setattr(vnstock_worker,"get_vnstock_api_key",lambda:None)
    monkeypatch.setattr(sys,"stdin",io.StringIO(json.dumps({"operation":"capture_raw","symbols":["FPT"]})))
    vnstock_worker.main()
    assert json.loads(capsys.readouterr().out)["error"]=="QUOTA_UNAVAILABLE"
    assert calls==[]
