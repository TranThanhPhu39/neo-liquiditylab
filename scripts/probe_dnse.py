"""Small read-only DNSE quote probe; does not enable collection or normalization."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

from config.settings import ROOT
from src.data_providers.dnse_readonly import DNSEReadOnly,DNSEHTTPError


def quote_shapes(value):
    found=[]
    def walk(node):
        if isinstance(node,dict):
            if isinstance(node.get("bid"),list) or isinstance(node.get("offer"),list):
                found.append({"symbol":node.get("symbol"),"bid_levels":len(node.get("bid") or []),
                    "ask_levels":len(node.get("offer") or []),"has_time_field":"time" in node})
            for item in node.values():
                if isinstance(item,(dict,list)):
                    walk(item)
        elif isinstance(node,list):
            for item in node:
                walk(item)
    walk(value)
    return found


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols",nargs="+",default=["FPT","HPG","ACB"])
    parser.add_argument("--report",default="report/dnse_preflight.json")
    args=parser.parse_args()
    report={"provider":"dnse","status":"STARTING","units_verified":False,"finance_enabled":False,
        "probe_at_utc":datetime.now(timezone.utc).isoformat(),"purpose":"READ_ONLY_MARKET_DIAGNOSTIC","samples":[]}
    code=0
    try:
        if len(args.symbols)>3:
            raise ValueError("Initial DNSE probe is limited to three symbols")
        client=DNSEReadOnly()
        for symbol in args.symbols:
            observation=client.get(f"/price/{symbol}/quotes/latest",{"boardId":"G1"})
            artifact=ROOT/"data/raw"/f"dnse_probe_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.json"
            artifact.parent.mkdir(parents=True,exist_ok=True)
            artifact.write_text(json.dumps(observation,ensure_ascii=False,indent=2),encoding="utf-8")
            report["samples"].append({"symbol":symbol,"artifact":str(artifact),
                "received_at_utc":observation["received_at_utc"],"quote_shapes":quote_shapes(observation["payload"])})
        report["status"]="HTTP_JSON_PROBE_COMPLETED_NOT_UNIT_VERIFIED"
    except Exception as error:
        report.update(status="FAILED",error_category=type(error).__name__)
        if isinstance(error,DNSEHTTPError):
            report["http_status"]=error.status
        code=1
    path=Path(args.report)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return code


if __name__=="__main__":
    raise SystemExit(main())
