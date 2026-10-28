"""Bounded authenticated raw collector with status report and SQLite backup."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from config.settings import Settings
from src.data_pipeline.raw_collection import collect_raw_once, raw_quality_report
from src.data_pipeline.storage import Storage
from src.data_pipeline.validation import scheduled_session
from src.data_providers.base import LocalRateLimit
from src.models.orderbook import LOCAL_TZ


def capture_if_due(db, symbols, interval=180, capture=collect_raw_once):
    delay = db.collection_wait_seconds("vnstock",interval)
    if delay>0:
        return {"status":"WAITING_LOCAL_RATE_LIMIT","wait_seconds":delay+0.1}
    try:
        return capture(db,symbols)
    except LocalRateLimit:
        # A concurrent local caller may have reserved a run after our pre-check.
        return {"status":"WAITING_LOCAL_RATE_LIMIT","wait_seconds":db.collection_wait_seconds("vnstock",interval)+0.1}


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--symbols",nargs="+",default=["FPT","HPG","ACB"])
    parser.add_argument("--interval",type=int,default=180)
    parser.add_argument("--until",required=True,help="Aware ISO timestamp, e.g. 2026-10-28T14:30:00+07:00")
    parser.add_argument("--report",default="report/raw_collection_status.json")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.until)
    if end.tzinfo is None or args.interval<180 or end<=datetime.now(timezone.utc):
        parser.error("Cần until tương lai có timezone và interval >=180")
    if (end-datetime.now(timezone.utc)).total_seconds()>8*3600:
        parser.error("Mỗi lượt chạy tối đa 8 giờ; không lập lịch vô hạn")
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    exit_status, state = 0,"RUNNING"
    with Storage(args.db) as db:
        def checkpoint():
            report = raw_quality_report(db,args.interval)
            report.update(collector_state=state,planned_stop_local=end.astimezone(LOCAL_TZ).isoformat(),
                          report_updated_local=datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat())
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            return report
        try:
            while datetime.now(timezone.utc)<end:
                tick = time.monotonic()
                spacing_delay = None
                if scheduled_session(datetime.now(timezone.utc))=="SCHEDULED_CONTINUOUS":
                    result = capture_if_due(db,args.symbols,args.interval)
                    spacing_delay = result.get("wait_seconds")
                    print(json.dumps(result),flush=True)
                checkpoint()
                delay = spacing_delay if spacing_delay is not None else max(0,args.interval-(time.monotonic()-tick))
                next_tick = time.monotonic()+delay
                while time.monotonic()<next_tick and datetime.now(timezone.utc)<end:
                    time.sleep(min(5,max(0,next_tick-time.monotonic()),max(0,(end-datetime.now(timezone.utc)).total_seconds())))
            state = "COMPLETED"
        except KeyboardInterrupt:
            state = "STOPPED"
        except Exception as error:
            # Stop on source errors; no automatic quota/auth retries or source switching.
            state,exit_status = "FAILED",1
            print(json.dumps({"error_category":type(error).__name__}),file=sys.stderr,flush=True)
        finally:
            stamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")
            backup = db.backup_to(Path("data/backups")/f"liquidity_{stamp}.db")
            report = checkpoint()
            report["backup_path"] = backup
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"collector_state":state,"batches":report["batches"],"backup_path":backup}),flush=True)
    return exit_status


if __name__=="__main__":
    raise SystemExit(main())
