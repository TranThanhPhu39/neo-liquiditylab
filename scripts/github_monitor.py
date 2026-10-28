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
