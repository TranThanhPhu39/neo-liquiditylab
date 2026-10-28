"""Normalize stored raw only after a reviewed, versioned unit contract exists."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from config.settings import Settings
from src.data_pipeline.raw_migration import migrate_raw
from src.data_pipeline.storage import Storage
from src.data_pipeline.unit_contracts import load_unit_contract, UnitsNotVerified


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--contract",default="config/unit_contract.example.json")
    parser.add_argument("--batch-ids",nargs="*",type=int)
    parser.add_argument("--report",default="report/normalization_status.json")
    args = parser.parse_args()
    try:
        contract = load_unit_contract(args.contract)
        with Storage(args.db) as db:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            backup = db.backup_to(Path("data/backups")/f"before_normalization_{stamp}.db")
            result = migrate_raw(db,contract,args.batch_ids)
            result.update(backup_path=backup,integrity_check=db.integrity_check())
        code = 0
    except UnitsNotVerified as error:
        result = {"status":"BLOCKED_UNITS_UNVERIFIED","reason":str(error),"inserted":0,"raw_modified":False}
        code = 2
    path = Path(args.report)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return code


if __name__=="__main__":
    raise SystemExit(main())
