"""Audit, export and safely back up captured raw data without enabling finance."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from config.settings import Settings
from src.data_pipeline.raw_collection import raw_quality_report, replay_raw_at
from src.data_pipeline.storage import Storage
from src.models.orderbook import LOCAL_TZ


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--output",default="report/raw_collection_audit.json")
    parser.add_argument("--artifact-dir",default="data",help="Root for exported files and backups")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")
    with Storage(args.db) as db:
        report = raw_quality_report(db)
        batches = db.raw_batches()
        exported = []
        for batch in batches:
            for row in batch["payload"]["rows"]:
                exported.append({**row,"provider":batch["provider"],"upstream_source":batch["upstream_source"],
                                 "received_at_utc":batch["received_at_utc"],"units_status":"UNVERIFIED","source_timestamp":None})
        if exported:
            target = Path(args.artifact_dir)/"processed"/f"raw_quotes_{stamp}.csv"
            target.parent.mkdir(parents=True,exist_ok=True)
            columns = sorted({key for row in exported for key in row})
            with target.open("w",encoding="utf-8-sig",newline="") as stream:
                writer = csv.DictWriter(stream,fieldnames=columns)
                writer.writeheader()
                writer.writerows(exported)
            report["raw_export_path"] = str(target)
            first = datetime.fromisoformat(batches[0]["received_at_utc"])
            at = replay_raw_at(batches,first)
            report["replay_check"] = {"first_cutoff":first.isoformat(),"symbols_at_first_batch":sorted(at),
                "no_future_batches":all(row["received_at_utc"]<=first.isoformat() for row in at.values()),
                "repeat_is_deterministic":at==replay_raw_at(batches,first),"finance_run":False}
        report["backup_path"] = db.backup_to(Path(args.artifact_dir)/"backups"/f"audit_{stamp}.db")
        report["audited_at_local"] = datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat()
    path = Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
