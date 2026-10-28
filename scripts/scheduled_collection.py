"""One local trading-day job, launched by Windows Task Scheduler; no trading orders."""
import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import time

from config.settings import ROOT
from scripts.market_preflight import sdk_request
from src.data_pipeline.market_schedule import build_universe, chunks, in_window, open_exchanges, quota_plan, windows_for
from src.data_pipeline.raw_collection import collect_raw_once
from src.data_pipeline.raw_migration import migrate_raw
from src.data_pipeline.storage import Storage
from src.data_pipeline.unit_contracts import load_unit_contract
from src.data_providers.diagnostics import MARKET_FIELDS
from src.models.orderbook import LOCAL_TZ


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(path)


@contextmanager
def exclusive_job(path):
    """OS-backed lock is released even when the process is killed."""
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell()==0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name=="nt":
            import msvcrt
            acquire=lambda:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            release=lambda:msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            acquire=lambda:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            release=lambda:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        try:
            acquire()
        except OSError:
            raise RuntimeError("ANOTHER_COLLECTION_JOB_IS_RUNNING") from None
        try:
            yield
        finally:
            stream.seek(0)
            release()


def stop_reason(config):
    current=json.loads((ROOT/config.get("_config_path","config/market_collection.json")).read_text(encoding="utf-8"))
    if not current.get("enabled",False):
        return "DISABLED"
    if (ROOT/config["neo_ready_file"]).exists():
        return "NEO_HANDOFF_READY"
    if (ROOT/config["stop_file"]).exists():
        return "STOP_REQUESTED"
    return None


def wait_until(target, config):
    while datetime.now(LOCAL_TZ)<target:
        if stop_reason(config):
            return False
        time.sleep(min(5,max(0,(target-datetime.now(LOCAL_TZ)).total_seconds())))
    return True


def daily_audit(db, day, folder):
    """Stream today's rows to CSV rather than materializing market history in RAM."""
    start=datetime.combine(day,datetime.min.time(),LOCAL_TZ).astimezone(timezone.utc)
    end=start+timedelta(days=1)
    export=folder/"raw_quotes.csv"
    counts=Counter()
    seen=set()
    observations=batches=0
    with export.open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=MARKET_FIELDS+["raw_batch_id","received_at_utc","source_timestamp","units_status"])
        writer.writeheader()
        cursor=db.connection.execute("SELECT id,received_at_utc,payload FROM raw_quote_batches WHERE provider='vnstock' AND received_at_utc>=? AND received_at_utc<? ORDER BY received_at_utc,id",(start.isoformat(),end.isoformat()))
        for batch in cursor:
            batches+=1
            for row in json.loads(batch["payload"])["rows"]:
                observations+=1
                seen.add(row["symbol"])
                counts[row.get("exchange","UNKNOWN")]+=1
                writer.writerow({**{k:row.get(k) for k in MARKET_FIELDS},"raw_batch_id":batch["id"],
                    "received_at_utc":batch["received_at_utc"],"source_timestamp":None,"units_status":"RAW_SOURCE_VALUES"})
    normalized=db.connection.execute("SELECT COUNT(*) FROM orderbook_snapshots WHERE provider='vnstock' AND collected_at_utc>=? AND collected_at_utc<?",(start.isoformat(),end.isoformat())).fetchone()[0]
    result={"batches":batches,"symbol_observations":observations,"distinct_symbols":len(seen),
        "exchange_observations":dict(counts),"normalized_snapshots":normalized,"csv":str(export),
        "source_timestamp_verified":False,"integrity_check":db.integrity_check()}
    write_json(folder/"audit.json",result)
    return result


def compressed_backup(db, folder):
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/("market_"+datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S_%f")+".db")
    db.backup_to(target)
    archive=target.with_suffix(".db.gz")
    with target.open("rb") as source,gzip.open(archive,"xb",compresslevel=6) as dest:
        shutil.copyfileobj(source,dest,length=1024*1024)
    # Remove only the uncompressed temporary backup created by this call.
    target.resolve().relative_to((ROOT/"data/backups").resolve())
    target.unlink()
    return str(archive)


def run_day(config, *, preflight=False, resume_after_failure=False, checkpoint_hook=None):
    now=datetime.now(LOCAL_TZ)
    day=now.date()
    folder=ROOT/"report/automation"/day.isoformat()
    folder.mkdir(parents=True,exist_ok=True)
    latest=ROOT/"report/automation/latest.json"
    record={"date_local":day.isoformat(),"timezone":"Asia/Ho_Chi_Minh","scope":"HOSE_HNX_UPCOM_EQUITIES",
            "state":"STARTING","source_timestamp_verified":False,"market_orders_sent":0}
    def checkpoint():
        record["updated_at_local"]=datetime.now(LOCAL_TZ).isoformat()
        write_json(folder/"status.json",record)
        write_json(latest,record)
        if checkpoint_hook:
            checkpoint_hook(record)
    reason=stop_reason(config)
    end=windows_for(day,config)[-1][1]
    start=datetime.combine(day,datetime.strptime(config.get("start_time","09:10"),"%H:%M").time(),LOCAL_TZ)
    if reason or day.weekday()>=5 or day.isoformat() in config["holidays"] or ((now>=end or now<start) and not preflight):
        record["state"]=reason or ("SKIPPED_NONTRADING_DAY" if day.weekday()>=5 or day.isoformat() in config["holidays"] else
            "SKIPPED_BEFORE_START" if now<start else "SKIPPED_AFTER_COLLECTION_WINDOW")
        record["updated_at_local"]=now.isoformat()
        prior_path=folder/"status.json"
        if prior_path.exists():
            previous=json.loads(prior_path.read_text(encoding="utf-8"))
            for key in ("plan","exchange_counts","normalized_symbols"):
                if key in previous:
                    record[key]=previous[key]
            record["previous_daily_state"]=previous.get("state")
        # Never replace a completed day's original status with a late logon skip.
        write_json(folder/"last_launch_check.json",record)
        write_json(latest,record)
        return 0
    prior=folder/"status.json"
    if prior.exists() and not resume_after_failure and not preflight:
        previous=json.loads(prior.read_text(encoding="utf-8"))
        if previous.get("state") in ("FAILED","COMPLETED","COMPLETED_NO_QUOTES","STOP_REQUESTED","NEO_HANDOFF_READY"):
            write_json(latest,{**previous,"relaunch":"SKIPPED_ALREADY_FINISHED_OR_FAILED"})
            return 0
        if previous.get("state") in ("RUNNING","WAITING"):
            record["previous_process_interrupted"]=True
    checkpoint()
    exit_code=0
    with Storage(ROOT/config["database"]) as db:
        try:
            if shutil.disk_usage(ROOT).free < float(config["minimum_free_disk_gb"])*1024**3:
                raise RuntimeError("INSUFFICIENT_DISK_SPACE")
            reference=sdk_request("universe")
            universe,excluded=build_universe(reference,config["exchanges"])
            write_json(folder/"universe.json",{**reference,"rows":universe,"excluded":excluded})
            plan=quota_plan(len(universe),reference["account"],config)
            record.update(plan=plan,exchange_counts=dict(Counter(r["exchange"] for r in universe)),
                          normalized_symbols=config["normalize_symbols"],completed_sweeps=0,partial_sweeps=0,
                          batches_captured=0,observations_captured=0,missing_symbols_latest_sweep=[])
            if not set(config["normalize_symbols"]) <= {"FPT","HPG","ACB"}:
                raise ValueError("UNIT_REVIEW_SCOPE_NOT_EXTENDED")
            contract=load_unit_contract(ROOT/config["unit_contract"])
            if preflight:
                record["state"]="PREFLIGHT_PASSED_NO_QUOTES"
                checkpoint()
                return 0
            while datetime.now(LOCAL_TZ)<end:
                reason=stop_reason(config)
                if reason:
                    record["state"]=reason
                    break
                now=datetime.now(LOCAL_TZ)
                if not in_window(now,config):
                    record["state"]="WAITING"
                    checkpoint()
                    if not wait_until(min(end,now+timedelta(seconds=30)),config):
                        continue
                    continue
                if shutil.disk_usage(ROOT).free < float(config["minimum_free_disk_gb"])*1024**3:
                    raise RuntimeError("INSUFFICIENT_DISK_SPACE")
                status=sdk_request("market_status")
                write_json(folder/"market_status_latest.json",status)
                opened=open_exchanges(status,datetime.now(LOCAL_TZ))
                if not opened:
                    record["state"]="WAITING_MARKET_CLOSED"
                    checkpoint()
                    wait_until(min(end,datetime.now(LOCAL_TZ)+timedelta(seconds=180)),config)
                    continue
                active=[r for r in universe if r["exchange"] in opened]
                groups=chunks(active,config["batch_size"])
                cycle_start=datetime.now(LOCAL_TZ)
                cycle_missing=[]
                sweep_done=0
                record["state"]="RUNNING"
                record["sweep_started_at_local"]=cycle_start.isoformat()
                for index,group in enumerate(groups):
                    # Fixed batch slots give the same symbol a full cycle between polls.
                    due=cycle_start+timedelta(seconds=index*plan["batch_spacing_seconds"])
                    if not wait_until(min(due,end),config) or not in_window(datetime.now(LOCAL_TZ),config):
                        break
                    delay=db.collection_wait_seconds("vnstock",config["minimum_batch_spacing_seconds"])
                    if delay and not wait_until(min(end,datetime.now(LOCAL_TZ)+timedelta(seconds=delay+0.1)),config):
                        break
                    if not in_window(datetime.now(LOCAL_TZ),config):
                        break
                    symbols=[r["symbol"] for r in group]
                    result=collect_raw_once(db,symbols,min_interval=config["minimum_batch_spacing_seconds"])
                    if result["status"]=="PARTIAL_RAW" and not result["missing_symbols"]:
                        raise RuntimeError("QUOTE_SCHEMA_CHANGED")
                    batch_ids=[r[0] for r in db.connection.execute("SELECT id FROM raw_quote_batches WHERE run_id=?",(result["run_id"],))]
                    migrate_raw(db,contract,batch_ids,symbols=set(config["normalize_symbols"]))
                    record["batches_captured"]+=1
                    record["observations_captured"]+=result["inserted_rows"]
                    record["latest_received_at_utc"]=result["received_at_utc"]
                    record["batch_in_sweep"]=index+1
                    cycle_missing.extend(result["missing_symbols"])
                    sweep_done+=1
                    checkpoint()
                record["missing_symbols_latest_sweep"]=sorted(cycle_missing)
                record["completed_sweeps" if sweep_done==len(groups) else "partial_sweeps"]+=1
                record["latest_sweep_duration_seconds"]=(datetime.now(LOCAL_TZ)-cycle_start).total_seconds()
                record["last_open_exchanges"]=sorted(opened)
                checkpoint()
                wait_until(min(end,cycle_start+timedelta(seconds=plan["cycle_seconds"])),config)
            if record["state"] not in ("STOP_REQUESTED","NEO_HANDOFF_READY","DISABLED"):
                record["state"]=stop_reason(config) or ("COMPLETED" if record["batches_captured"] else "COMPLETED_NO_QUOTES")
        except KeyboardInterrupt:
            record["state"]="STOP_REQUESTED"
        except Exception as error:
            record.update(state="FAILED",error_category=type(error).__name__)
            # Only internal categories, never arbitrary SDK log/headers.
            if isinstance(error,RuntimeError) and str(error).isupper():
                record["error_code"]=str(error)
            exit_code=1
        finally:
            if not preflight:
                try:
                    record["audit"]=daily_audit(db,day,folder)
                    record["backup_path"]=compressed_backup(db,ROOT/"data/backups/automation")
                    record["raw_unverified_scope_note"]="All equities stored raw; A1/B5 normalization restricted to reviewed FPT/HPG/ACB."
                except Exception as error:
                    record.update(state="FAILED",finalization_error_category=type(error).__name__)
                    exit_code=1
            checkpoint()
    return exit_code


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight",action="store_true",help="Refresh universe/quotas only; no market quotes")
    parser.add_argument("--resume-after-failure",action="store_true",help="Explicit operator recovery only")
    args=parser.parse_args()
    config=json.loads((ROOT/"config/market_collection.json").read_text(encoding="utf-8"))
    with exclusive_job(ROOT/"data/control/market_collection.lock"):
        return run_day(config,preflight=args.preflight,resume_after_failure=args.resume_after_failure)


if __name__=="__main__":
    raise SystemExit(main())
