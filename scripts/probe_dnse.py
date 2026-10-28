"""Small read-only DNSE quote probe; does not enable collection or normalization."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from config.settings import ROOT
from src.data_providers.dnse_readonly import DNSEReadOnly,DNSEHTTPError

VN=ZoneInfo("Asia/Ho_Chi_Minh")


def history_window(day,start="11:29",end="11:30"):
    beginning=datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=VN)
    ending=datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=VN)
    if not 0<(ending-beginning).total_seconds()<=300:
        raise ValueError("History diagnostic must cover at most five minutes")
    if ending>datetime.now(VN):
        raise ValueError("History diagnostic cannot request a future interval")
    return {"from":int(beginning.timestamp()),"to":int(ending.timestamp())}


def observation_summary(observation,symbol,window=None):
    payload=observation["payload"]
    quotes=payload.get("quotes") if isinstance(payload,dict) else None
    if not isinstance(quotes,list):
        raise ValueError("DNSE_QUOTES_SCHEMA_MISSING")
    if any(not isinstance(q,dict) or q.get("symbol")!=symbol or q.get("boardId")!="G1"
           or not isinstance(q.get("bid"),list) or not isinstance(q.get("offer"),list)
           for q in quotes):
        raise ValueError("DNSE_QUOTES_SCOPE_OR_SCHEMA_MISMATCH")
    times=[q["time"] for q in quotes if isinstance(q.get("time"),str)]
    if window:
        if len(times)!=len(quotes):
            raise ValueError("DNSE_HISTORY_TIME_MISSING")
        for value in times:
            timestamp=datetime.strptime(value,"%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=VN).timestamp()
            if not window["from"]<=timestamp<=window["to"]:
                raise ValueError("DNSE_HISTORY_TIME_OUTSIDE_REQUEST")
    return {"quote_count":len(quotes),"quote_shapes":quote_shapes(payload),
            "source_time_min":min(times) if times else None,"source_time_max":max(times) if times else None,
            "source_timezone":"GMT+7 per official OpenAPI spec; raw time preserved",
            "has_next_page":bool(payload.get("nextPageToken")),
            "rate_limit_headers":observation.get("rate_limit_headers",{})}


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
    parser.add_argument("--history-date",help="Optional bounded historical diagnostic, YYYY-MM-DD")
    parser.add_argument("--history-start",default="11:29")
    parser.add_argument("--history-end",default="11:30")
    args=parser.parse_args()
    report={"provider":"dnse","status":"STARTING","units_verified":False,"finance_enabled":False,
        "probe_at_utc":datetime.now(timezone.utc).isoformat(),"purpose":"READ_ONLY_MARKET_DIAGNOSTIC","samples":[]}
    code=0
    try:
        if not 1<=len(args.symbols)<=3 or len(set(args.symbols))!=len(args.symbols) or any(
                not re.fullmatch(r"[A-Z0-9]{1,12}",symbol) for symbol in args.symbols):
            raise ValueError("Initial DNSE probe needs one to three unique symbols")
        window=history_window(args.history_date,args.history_start,args.history_end) if args.history_date else None
        if window:
            report["history_window"]={**window,"timezone":"Asia/Ho_Chi_Minh","max_pages_per_symbol":2,"page_limit":2}
        client=DNSEReadOnly()
        for symbol in args.symbols:
            params={"boardId":"G1",**({**window,"limit":2,"order":"DESC"} if window else {})}
            seen_tokens=set()
            seen_quotes=set()
            for page in range(2 if window else 1):
                observation=client.get(f"/price/{symbol}/quotes"+("" if window else "/latest"),params)
                artifact=ROOT/"data/raw"/f"dnse_probe_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.json"
                artifact.parent.mkdir(parents=True,exist_ok=True)
                artifact.write_text(json.dumps(observation,ensure_ascii=False,indent=2),encoding="utf-8")
                summary=observation_summary(observation,symbol,window)
                if window:
                    page_quotes={json.dumps(q,sort_keys=True) for q in observation["payload"]["quotes"]}
                    if page_quotes & seen_quotes:
                        raise ValueError("DNSE_HISTORY_PAGES_OVERLAP")
                    seen_quotes.update(page_quotes)
                report["samples"].append({"symbol":symbol,"page":page+1,"artifact":str(artifact),
                    "received_at_utc":observation["received_at_utc"],**summary})
                token=observation["payload"].get("nextPageToken")
                if not window or not token:
                    break
                if not isinstance(token,str) or len(token)>4096 or token in seen_tokens:
                    raise ValueError("DNSE_INVALID_OR_REPEATED_PAGE_TOKEN")
                seen_tokens.add(token)
                params["nextPageToken"]=token
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
