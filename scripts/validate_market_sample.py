import argparse
import json
from pathlib import Path
import sys

from config.settings import Settings
from src.data_pipeline.storage import Storage
from src.finance.data_verification import verify_raw_prices


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate actual raw price arithmetic without assuming volume units")
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--report",default="report/market_sample_validation.json")
    args = parser.parse_args()
    with Storage(args.db) as db:
        report = verify_raw_prices(db)
    path = Path(args.report)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="checks"},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
