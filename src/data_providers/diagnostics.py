"""Small allowlists for SDK diagnostics; never serialize account objects or logs."""
import re


MARKET_FIELDS = ["symbol", "exchange", "reference_price", "close_price"] + [
    f"{side}_{kind}_{level}" for side in ("bid", "ask") for kind in ("price", "vol") for level in range(1, 4)]


def account_summary(status, usage):
    def counts(value):
        return {k:v for k,v in (value or {}).items() if k in
                ("per_minute","per_hour","per_day","per_month","minute","hour","day","month")
                and isinstance(v,(int,float)) and not isinstance(v,bool)}
    return {"tier":status.get("tier"), "authenticated":status.get("authenticated") is True,
            "limits":counts(usage.get("limits")), "used":counts(usage.get("used"))}


def error_summary(error):
    code = getattr(error,"code",None)
    return {"error":type(error).__name__,
            "error_code":code if isinstance(code,str) and re.fullmatch(r"[A-Z0-9_]{1,100}",code) else None,
            "retryable":getattr(error,"retryable",False) is True}


def quote_evidence(frame):
    columns = [str(c) for c in frame.columns]
    # Only known market fields may leave the worker; extra account columns are omitted.
    allowed = [c for c in MARKET_FIELDS if c in frame.columns]
    import json
    metadata = {k:v for k,v in frame.attrs.items() if k in
                ("source","route","price_unit","volume_unit","currency") and isinstance(v,(str,int,float,bool))}
    return {"rows":json.loads(frame[allowed].to_json(orient="records")),
            "columns": [c for c in MARKET_FIELDS if c in columns],
            "missing_columns":sorted(set(MARKET_FIELDS)-set(columns)), "metadata":metadata}
