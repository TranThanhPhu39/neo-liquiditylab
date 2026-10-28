"""GitHub Actions entry with explicit preflight, daily partitions and encrypted checkpoints."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import time

from config.settings import ROOT,get_vnstock_api_key
from scripts.github_checkpoint import ReleaseStore,state_key
from scripts.market_preflight import sdk_request
from scripts.scheduled_collection import exclusive_job,run_day,write_json
from src.data_pipeline.storage import Storage
from src.models.orderbook import LOCAL_TZ


REPOSITORY="TranThanhPhu39/neo-liquiditylab"


def require_environment():
    if os.environ.get("GITHUB_REPOSITORY")!=REPOSITORY or os.environ.get("GITHUB_REF")!="refs/heads/main":
        raise RuntimeError("UNEXPECTED_REPOSITORY_OR_REF")
    if not get_vnstock_api_key():
        raise RuntimeError("VNSTOCK_API_KEY_SECRET_REQUIRED")


def preflight():
    require_environment()
    reference=sdk_request("universe")
    quote=sdk_request("probe",symbols=["FPT","HPG","ACB"])
    if not quote.get("market_request_succeeded") or quote.get("missing_columns") or len(quote.get("rows",[]))!=3:
        raise RuntimeError("MARKET_PREFLIGHT_FAILED")
    result={"status":"PASSED","scope":"LIVE_CI_CONNECTIVITY_DIAGNOSTIC_ONLY",
        "received_at_utc":quote["received_at_utc"],"provider_version":quote["version"],
        "authenticated":quote.get("account",{}).get("authenticated") is True,
        "reference_rows":len(reference["rows"]),"quote_symbols":sorted(r["symbol"] for r in quote["rows"]),
        "collection_history_added":False,"source_timestamp_verified":False,
        "note":"One CI runner only; a second fresh runner must also pass before claiming recurring authentication works."}
    write_json(ROOT/"report/github/preflight.json",result)
    print(json.dumps(result))


def collect(resume=False):
    require_environment()
    day=datetime.now(LOCAL_TZ).date().isoformat()
    store=ReleaseStore(REPOSITORY,state_key(os.environ.get("LIQUIDITYLAB_STATE_KEY","")))
    config=json.loads((ROOT/"config/market_collection.json").read_text(encoding="utf-8"))
    config.update(database=f"data/github/{day}.db",unit_contract="config/unit_contract.github-vnstock-kbs-5.2.1.json",
                  _config_path="data/control/github_collection.json")
    write_json(ROOT/config["_config_path"],config)
    database=ROOT/config["database"]
    reports=ROOT/"report/automation"/day
    restored=store.restore(day,database,reports)
    print(json.dumps({"restored_checkpoint":restored,"date_local":day}))
    run_id=os.environ["GITHUB_RUN_ID"]
    attempt=os.environ.get("GITHUB_RUN_ATTEMPT","1")
    last_saved=[0.0]
    def save(record,force=False):
        if not database.exists() or not record.get("batches_captured"):
            return
        if not force and (record.get("state")!="RUNNING" or time.monotonic()-last_saved[0]<900):
            return
        tag=store.publish(day,database,reports,run_id,attempt)
        last_saved[0]=time.monotonic()
        write_json(ROOT/"report/github/checkpoint.json",{"tag":tag,"date_local":day,"state":record.get("state")})
        print(json.dumps({"checkpoint_saved":tag}))
    code=1
    with exclusive_job(ROOT/"data/control/market_collection.lock"):
        try:
            code=run_day(config,resume_after_failure=resume,checkpoint_hook=save)
        finally:
            status_path=reports/"status.json"
            record=json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
            save(record,force=True)
            if record.get("state")=="FAILED":
                code=1
    return code


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["preflight","collect"])
    parser.add_argument("--resume-after-failure",action="store_true")
    args=parser.parse_args()
    try:
        if args.mode=="preflight":
            preflight()
            return 0
        return collect(args.resume_after_failure)
    except Exception as error:
        # Never echo arbitrary authentication/network errors with possible request details.
        report={"status":"FAILED","error_category":type(error).__name__}
        if isinstance(error,RuntimeError) and str(error).isupper():
            report["error_code"]=str(error)
        write_json(ROOT/"report/github/failure.json",report)
        print(json.dumps(report))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
