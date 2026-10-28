"""Whole-market planning: bounded batches, quota budgets and local time windows."""
from datetime import datetime, time
import math
import re

from src.models.orderbook import LOCAL_TZ


def build_universe(response, exchanges):
    rows,seen,excluded = [],{},[]
    for row in response.get("rows",[]):
        symbol,exchange = str(row.get("symbol","")).strip().upper(),str(row.get("exchange","")).strip().upper()
        if exchange not in exchanges or not re.fullmatch(r"[A-Z0-9]{1,12}",symbol):
            excluded.append({"symbol":symbol,"exchange":exchange})
            continue
        if symbol in seen and seen[symbol]!=exchange:
            raise ValueError("Conflicting exchange for symbol")
        if symbol not in seen:
            rows.append({"symbol":symbol,"exchange":exchange})
            seen[symbol]=exchange
    if not rows or not set(exchanges)<=set(seen.values()):
        raise ValueError("Universe missing an exchange or empty")
    return sorted(rows,key=lambda row:(row["exchange"],row["symbol"])),excluded


def chunks(rows, size):
    if not isinstance(size,int) or not 1<=size<=100:
        raise ValueError("Batch size must be 1..100")
    return [rows[start:start+size] for start in range(0,len(rows),size)]


def windows_for(day, config):
    return [(datetime.combine(day,time.fromisoformat(a),LOCAL_TZ),
             datetime.combine(day,time.fromisoformat(b),LOCAL_TZ)) for a,b in config["collection_windows"]]


def in_window(now, config):
    local=now.astimezone(LOCAL_TZ)
    return local.weekday()<5 and local.date().isoformat() not in config["holidays"] and any(
        start<=local<end for start,end in windows_for(local.date(),config))


def quota_plan(symbol_count, account, config):
    if symbol_count<=0 or int(config["target_cycle_seconds"])<180 or float(config["minimum_batch_spacing_seconds"])<5 or int(config["monthly_reserve_sessions"])<1:
        raise ValueError("Invalid collection cadence")
    chunks([None]*symbol_count,config["batch_size"])
    n=math.ceil(symbol_count/config["batch_size"])
    utilization=float(config["quota_utilization"])
    if not 0<utilization<=0.8 or not account.get("authenticated"):
        raise ValueError("Invalid quota utilization or authentication")
    budgets={}
    for window in ("minute","hour","day","month"):
        limit,used=account.get("limits",{}).get("per_"+window),account.get("used",{}).get(window)
        if not isinstance(limit,(int,float)) or not isinstance(used,(int,float)) or limit<=used:
            raise ValueError("Missing or exhausted quota")
        budgets[window]=math.floor((limit-used)*utilization)
    daily_seconds=sum((end-start).total_seconds() for start,end in windows_for(datetime.now(LOCAL_TZ).date(),config))
    # A status request per sweep and one daily reference request also consume quota.
    per_day=min(budgets["day"],budgets["month"]//int(config["monthly_reserve_sessions"]))-1
    if per_day<n+1 or min(budgets["minute"],budgets["hour"])<=0:
        raise ValueError("Insufficient budget for one complete market sweep")
    cycle=math.ceil(max(int(config["target_cycle_seconds"]),
        n*float(config["minimum_batch_spacing_seconds"]),
        60*(n+1)/budgets["minute"],3600*(n+1)/budgets["hour"],daily_seconds*(n+1)/per_day))
    return {"symbols":symbol_count,"batches_per_sweep":n,"batch_size":config["batch_size"],
        "cycle_seconds":cycle,"batch_spacing_seconds":cycle/n,
        "estimated_daily_sdk_calls":math.ceil(daily_seconds/cycle)*(n+1)+1,
        "quota_budgets":budgets,"monthly_sessions_reserved":config["monthly_reserve_sessions"]}


def open_exchanges(response, now):
    result=set()
    for row in response.get("rows",[]):
        if row.get("asset_type")!="EQUITY" or row.get("status")!="OPEN":
            continue
        stamp=row.get("timestamp")
        if not isinstance(stamp,(float,int)) or abs(now.timestamp()-stamp/1000)>120:
            raise ValueError("Stale builtin market status")
        result.add(row["exchange"])
    return result
