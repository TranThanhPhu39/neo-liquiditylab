import argparse
import json
import sys
from pathlib import Path

from config.settings import Settings
from src.data_pipeline.daily_report import build_daily_report
from src.data_pipeline.storage import Storage


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Create a descriptive Markdown/HTML report of actual raw collection")
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--status",default="report/raw_collection_2026-10-28.json")
    parser.add_argument("--output-stem",default="report/daily_2026-10-28")
    args = parser.parse_args()
    try:
        status = json.loads(Path(args.status).read_text(encoding="utf-8"))
    except (OSError,ValueError):
        status = {}
    with Storage(args.db) as db:
        result = build_daily_report(db,args.output_stem,status)
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
