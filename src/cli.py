import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
from pathlib import Path
import sys
import time

import yaml

from config.settings import ROOT, Settings
from src.data_pipeline.collector import collect_once
from src.data_pipeline.quality import quality_report
from src.data_pipeline.storage import Storage
from src.data_pipeline.validation import scheduled_session
from src.data_providers import create_provider


def main(default_command=None):
    # PowerShell redirected stdout can otherwise use a legacy Windows code page.
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    settings = Settings()
    parser = argparse.ArgumentParser(description="NEO LiquidityLab — snapshot collection & audit")
    parser.add_argument("command", choices=["check", "once", "collect", "validate", "export"])
    parser.add_argument("--source", choices=["demo", "vnstock", "neo"], default=settings.data_source)
    parser.add_argument("--db", default=settings.database_path)
    parser.add_argument("--symbols", nargs="+", default=yaml.safe_load((ROOT/"config/symbols.yaml").read_text(encoding="utf-8"))["symbols"])
    parser.add_argument("--symbol")
    parser.add_argument("--start", help="ISO timestamp with timezone")
    parser.add_argument("--end", help="ISO timestamp with timezone")
    parser.add_argument("--duration", type=float, default=3600, help="seconds")
    parser.add_argument("--interval", type=int, default=settings.interval)
    parser.add_argument("--retries", type=int, choices=range(3), default=0)
    parser.add_argument("--include-outside-session", action="store_true")
    parser.add_argument("--holidays", nargs="*", default=[], help="Explicit closure dates YYYY-MM-DD; schedule is not an exchange calendar")
    parser.add_argument("--output", default="data/processed/export.csv")
    args = parser.parse_args(([default_command] + sys.argv[1:]) if default_command else None)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "check":
            versions = {}
            for name in ("streamlit", "pandas", "plotly", "pytest", "vnstock"):
                try:
                    versions[name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    versions[name] = "NOT INSTALLED"
            print(json.dumps({"python": sys.version, "packages": versions,
                "provider": create_provider(args.source).health_check()}, indent=2, ensure_ascii=False))
            return
        with Storage(args.db) as db:
            if args.command == "validate":
                report = quality_report(db, provider=args.source, expected_interval=args.interval)
                report["integrity_check"] = db.integrity_check()
                output = json.dumps(report, indent=2, ensure_ascii=False)
                print(output)
                if args.output != "data/processed/export.csv":
                    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
                    Path(args.output).write_text(output,encoding="utf-8")
            elif args.command == "export":
                import pandas as pd
                records = [s.to_record() for s in db.query(symbol=args.symbol, provider=args.source, start=args.start, end=args.end)]
                if not records:
                    print("NO DATA — không tạo tập dữ liệu giả")
                    return
                frame = pd.DataFrame(records)
                path = Path(args.output)
                path.parent.mkdir(parents=True,exist_ok=True)
                if path.suffix.lower() == ".parquet":
                    frame.to_parquet(path,index=False)
                else:
                    frame.to_csv(path,index=False,encoding="utf-8-sig")
                print(f"Exported {len(frame)} snapshots: {path}")
            else:
                provider = create_provider(args.source)
                if args.command == "once":
                    print(json.dumps(collect_once(provider,db,args.symbols,retries=args.retries)))
                else:
                    if args.interval < 180 or args.duration <= 0:
                        raise ValueError("Interval tối thiểu 180 giây; duration phải dương")
                    deadline = time.monotonic() + args.duration
                    while time.monotonic() < deadline:
                        tick = time.monotonic()
                        if args.source == "demo" or args.include_outside_session or scheduled_session(datetime.now(timezone.utc),args.holidays) == "SCHEDULED_CONTINUOUS":
                            try:
                                print(json.dumps(collect_once(provider,db,args.symbols,retries=args.retries)), flush=True)
                            except Exception as error:
                                # Keep loop alive on transient faults; NotConfigured is a hard stop.
                                from src.data_providers.base import NotConfigured
                                if isinstance(error,NotConfigured):
                                    raise
                                logging.error("Collector request failed category=%s",type(error).__name__)
                        else:
                            logging.info("Outside scheduled window — no request. Check exchange holidays separately.")
                        delay = min(max(0,args.interval-(time.monotonic()-tick)),max(0,deadline-time.monotonic()))
                        time.sleep(delay)
    except KeyboardInterrupt:
        print("Collector đã dừng an toàn.")
    except Exception as error:
        from src.data_providers.base import NotConfigured, ProviderError
        # Only our own sanitized configuration messages are displayed.
        message = str(error) if isinstance(error,(NotConfigured,ProviderError,ValueError)) else type(error).__name__
        print(f"ERROR: {message}",file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
