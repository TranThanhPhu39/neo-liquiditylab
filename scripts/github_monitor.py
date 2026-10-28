"""Read only sanitized worker JSON summaries and pytest totals from GitHub job logs."""
import argparse
import json
from pathlib import Path
import re

from config.settings import ROOT
from scripts.github_api import GitHubAPI,repository_path
from scripts.github_setup import REPOSITORY

SAFE_FIELDS={"status","scope","received_at_utc","provider_version","authenticated","reference_rows",
    "quote_symbols","collection_history_added","source_timestamp_verified","note","checkpoint",
    "restored_previous_runner_checkpoint","contains_market_observations","error_category","error_code",
    "sdk_error_category","sdk_error_code","checkpoint_saved","restored_checkpoint","date_local",
    "universe_symbols","symbols_requested","symbols_returned","exchange_counts","missing_symbols"}
DNSE_FIELDS={"provider","status","units_verified","finance_enabled","probe_at_utc","purpose",
    "samples","history_window","error_category","http_status","error_code"}
DNSE_NETWORK_FIELDS={"provider","status","purpose","checked_at_utc","host","attempts",
    "resolved_addresses","error_code"}


def dnse_summary(candidate):
    result={key:value for key,value in candidate.items() if key not in ("samples","history_window")}
    result["mode"]="history" if "history_window" in candidate else "latest"
    result["samples"]=[]
    for sample in candidate.get("samples",[]):
        if not isinstance(sample,dict):
            continue
        fields={key:sample[key] for key in ("symbol","page","quote_count","source_time_min",
                "source_time_max","has_next_page") if key in sample}
        fields["quote_shapes"]=[{key:shape[key] for key in ("symbol","bid_levels","ask_levels","has_time_field")
                                if key in shape} for shape in sample.get("quote_shapes",[]) if isinstance(shape,dict)]
        result["samples"].append(fields)
    return result


def summarize_log(log):
    records=[]
    for line in log.splitlines():
        match=re.search(r"\{.*\}",line)
        if match:
            try:
                candidate=json.loads(match.group())
            except ValueError:
                continue
            if isinstance(candidate,dict) and candidate and set(candidate)<=SAFE_FIELDS:
                records.append(candidate)
            elif isinstance(candidate,dict) and candidate.get("provider")=="dnse" and set(candidate)<=DNSE_FIELDS:
                records.append(dnse_summary(candidate))
            elif isinstance(candidate,dict) and candidate.get("purpose")=="PUBLIC_NETWORK_DIAGNOSTIC" and set(candidate)<=DNSE_NETWORK_FIELDS:
                records.append(candidate)
    totals=[line for line in log.splitlines() if re.search(r"\b[0-9]+ passed\b",line)]
    return {"worker_summaries":records,"pytest_totals":totals[-3:]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id",type=int)
    args=parser.parse_args()
    api=GitHubAPI()
    base=repository_path(REPOSITORY)
    run=api.request("GET",base+f"/actions/runs/{args.run_id}")
    report={k:run[k] for k in ("id","name","status","conclusion","html_url","head_sha")}
    report["jobs"]=[]
    for job in api.request("GET",base+f"/actions/runs/{args.run_id}/jobs")["jobs"]:
        result={"name":job["name"],"conclusion":job["conclusion"],
            "steps":[{k:s[k] for k in ("name","status","conclusion")} for s in job.get("steps",[])]}
        result.update({key:job.get(key) for key in ("id","runner_id","runner_name","started_at","completed_at")})
        if job["status"]=="completed" and job["conclusion"]!="skipped":
            log=api.request("GET",base+f"/actions/jobs/{job['id']}/logs",binary=True).decode("utf-8",errors="replace")
            result.update(summarize_log(log))
        report["jobs"].append(result)
    path=ROOT/"report/github"/f"run_{args.run_id}.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
