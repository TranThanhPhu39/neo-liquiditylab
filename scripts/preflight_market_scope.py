"""One bounded whole-market diagnostic sweep, separate from collection history."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from scripts.market_preflight import sdk_request
from src.data_pipeline.market_schedule import build_universe, chunks, quota_plan


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe",required=True)
    parser.add_argument("--config",default="config/market_collection.json")
    parser.add_argument("--all-batches",action="store_true")
    parser.add_argument("--report",default="report/market_scope_preflight_2026-10-28.json")
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding="utf-8"))
    reference=json.loads(Path(args.universe).read_text(encoding="utf-8"))
    rows,excluded=build_universe(reference,config["exchanges"])
    plan=quota_plan(len(rows),reference["account"],config)
    batches=chunks(rows,config["batch_size"])
    results=[]
    chosen=batches if args.all_batches else batches[:1]
    for index,batch in enumerate(chosen):
        start=time.monotonic()
        requested=[row["symbol"] for row in batch]
        probe=sdk_request("probe",symbols=requested)
        if not probe.get("market_request_succeeded") or probe.get("missing_columns") or not probe.get("account",{}).get("authenticated"):
            raise RuntimeError("MARKET_BATCH_PREFLIGHT_FAILED")
        actual=[row["symbol"] for row in probe["rows"]]
        if len(actual)!=len(set(actual)) or not set(actual)<=set(requested):
            raise RuntimeError("UNEXPECTED_BATCH_SYMBOLS")
        artifact=Path("data/raw")/f"market_preflight_{index+1:02d}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        artifact.write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding="utf-8")
        result={"batch":index+1,"requested":len(requested),"returned":len(actual),
            "missing_symbols":sorted(set(requested)-set(actual)),"artifact":str(artifact),"received_at_utc":probe["received_at_utc"]}
        results.append(result)
        print(json.dumps(result),flush=True)
        if index+1<len(chosen):
            time.sleep(max(0,plan["batch_spacing_seconds"]-(time.monotonic()-start)))
    report={"status":"PASSED","scope":"DIAGNOSTIC_ONLY_NOT_TRADING_SESSION_HISTORY",
        "universe_symbols":len(rows),"exchange_counts":dict(Counter(row["exchange"] for row in rows)),
        "excluded_reference_rows":excluded,"plan":plan,"batches_tested":len(results),
        "symbols_requested":sum(r["requested"] for r in results),"symbols_returned":sum(r["returned"] for r in results),
        "missing_symbols":sorted({s for r in results for s in r["missing_symbols"]}),"batches":results,
        "normalization_scope":config["normalize_symbols"],"source_timestamp_verified":False}
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({key:report[key] for key in ("status","universe_symbols","symbols_requested","symbols_returned","exchange_counts")}))


if __name__=="__main__":
    main()
