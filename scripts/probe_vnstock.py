"""Explicit one-shot SDK probe; does not enable or normalize live collection."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from config.settings import ROOT
from src.models.orderbook import LOCAL_TZ


def probe(*, quote=False, symbols=("FPT","HPG","ACB"), runner=subprocess.run):
    request = {"operation":"probe","quote":quote,"symbols":list(symbols)}
    report = {"probe_at_local":datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(),
              "provider":"vnstock", "upstream_source":"kbs", "symbols_requested":list(symbols),
              "purpose":"one-shot diagnostic; not a collection session", "units_verified":False,
              "collection_enabled":False}
    try:
        result = runner([sys.executable,"-m","src.data_providers.vnstock_worker"],input=json.dumps(request),
                        capture_output=True,text=True,timeout=45,cwd=ROOT)
        response = json.loads(result.stdout)
        if result.returncode:
            report["error"] = "WORKER_FAILED"
        else:
            report.update(response)
    except subprocess.TimeoutExpired:
        report.update(error="TIMEOUT",market_request_succeeded=False)
    except (ValueError,TypeError):
        report["error"] = "INVALID_WORKER_RESPONSE"
    return report


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quote",action="store_true",help="One explicit diagnostic quote; never loop this command")
    parser.add_argument("--symbols",nargs="+",default=["FPT","HPG","ACB"])
    parser.add_argument("--output",default="report/vnstock_preflight.json")
    args = parser.parse_args()
    report = probe(quote=args.quote,symbols=args.symbols)
    path = Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
