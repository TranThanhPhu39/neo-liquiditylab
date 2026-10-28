"""Close a bounded collection after it stops; no market-data requests."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from config.settings import Settings
from src.models.orderbook import LOCAL_TZ


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(path)


def read_status(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return {}


def close_collection(db_path,status_path,report_path,artifact_dir="data"):
    report_path = Path(report_path)
    audit_path = report_path.with_name(report_path.stem+"_audit.json")
    prices_path = report_path.with_name(report_path.stem+"_prices.json")
    for module,output in (("scripts.audit_raw_collection",audit_path),("scripts.validate_market_sample",prices_path)):
        command = [sys.executable,"-m",module,"--db",str(db_path),
                   "--output" if module.endswith("audit_raw_collection") else "--report",str(output)]
        if module.endswith("audit_raw_collection"):
            command += ["--artifact-dir",str(artifact_dir)]
        run = subprocess.run(command,capture_output=True,text=True,encoding="utf-8",timeout=60)
        if run.returncode:
            raise RuntimeError("Post-collection audit failed; external logs are not echoed")
    status = read_status(status_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    prices = json.loads(prices_path.read_text(encoding="utf-8"))
    completed = status.get("collector_state")=="COMPLETED"
    report = {"status":"COMPLETED_RAW_ONLY" if completed else "AUDITED_COLLECTION_END_UNCONFIRMED",
        "collector_state":status.get("collector_state","UNKNOWN"),
        "closed_at_local":datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(),
        "batches":audit["batches"],"symbol_observations":audit["symbol_observations"],
        "symbols":audit["symbols"],"integrity_check":audit["integrity_check"],
        "gap_candidates":audit["gap_candidates"],"backup_path":audit["backup_path"],
        "raw_export_path":audit.get("raw_export_path"),"audit_report":str(audit_path),
        "price_diagnostics_report":str(prices_path),"price_checks_passed":prices["price_checks_passed"],
        "price_checks_failed":prices["price_checks_failed"],"volume_units_verified":False,
        "raw_to_normalized_status":"BLOCKED_UNITS_UNVERIFIED",
        "execution_stress_portfolio_on_actual_data":"NOT_RUN_UNITS_UNVERIFIED",
        "scope_note":"Chốt phần cửa sổ giao dịch đã thực thu; không chứng nhận phiên đầy đủ hoặc đủ 5–10 phiên."}
    write_report(report_path,report)
    return report


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--collector-status",required=True)
    parser.add_argument("--at",required=True,help="Expected aware collection end timestamp")
    parser.add_argument("--report",required=True)
    args = parser.parse_args()
    at = datetime.fromisoformat(args.at)
    if at.tzinfo is None:
        parser.error("--at requires timezone")
    if at-datetime.now(timezone.utc)>timedelta(hours=8):
        parser.error("Maximum wait is 8 hours")
    write_report(args.report,{"status":"WAITING_FOR_COLLECTION_END","planned_end_local":at.astimezone(LOCAL_TZ).isoformat(),
                              "market_requests":0,"volume_units_verified":False})
    print(json.dumps({"status":"WAITING_FOR_COLLECTION_END","until":at.isoformat()}),flush=True)
    try:
        grace_end = at+timedelta(seconds=90)
        while datetime.now(timezone.utc)<grace_end:
            if read_status(args.collector_status).get("collector_state") in ("COMPLETED","FAILED","STOPPED"):
                break
            time.sleep(5)
        report = close_collection(args.db,args.collector_status,args.report)
        print(json.dumps({"status":report["status"],"batches":report["batches"],"report":args.report}),flush=True)
    except Exception as error:
        write_report(args.report,{"status":"FINALIZATION_FAILED","error_category":type(error).__name__,"market_requests":0})
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
