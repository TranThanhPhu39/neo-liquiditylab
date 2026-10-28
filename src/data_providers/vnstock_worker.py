"""Isolated SDK process: enforce timeout without leaving a hung request thread."""
import contextlib
import importlib.metadata
import io
import json
import sys

from config.settings import get_vnstock_api_key
from src.data_providers.diagnostics import account_summary, error_summary, quote_evidence


def main():
    request = json.loads(sys.stdin.read())
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            version = importlib.metadata.version("vnstock")
            if version != "5.2.1":
                raise RuntimeError("SDK_VERSION_UNVERIFIED")
            import vnstock
            from vnstock import Market
            api_key = get_vnstock_api_key()
            if api_key:
                vnstock.login(api_key)
            if request.get("operation") in ("universe", "market_status", "policy"):
                from datetime import datetime, timezone
                from vnstock import Reference
                summary = account_summary(vnstock.status(), vnstock.usage())
                if not summary["authenticated"]:
                    raise RuntimeError("AUTHENTICATION_REQUIRED")
                for window in ("minute", "hour", "day", "month"):
                    limit, used = summary["limits"].get(f"per_{window}"), summary["used"].get(window)
                    if limit is None or used is None or used >= limit:
                        raise RuntimeError("QUOTA_UNAVAILABLE")
                operation = request["operation"]
                response = {"version":version, "account":summary, "operation":operation}
                if operation == "universe":
                    frame = Reference().equity.list_by_exchange(source="vci")
                    response.update(rows=json.loads(frame[["symbol", "exchange"]].to_json(orient="records")),
                                    source="vci", method="Reference().equity.list_by_exchange")
                elif operation == "market_status":
                    frame = Reference().market.status()
                    response.update(rows=json.loads(frame.to_json(orient="records")),columns=list(frame.columns),source="builtin")
                else:
                    from vnstock.account import policy
                    # Policy rules are public tier limits, not account/device objects.
                    response["rule"] = policy().get("rule", {})
                response["received_at_utc"] = datetime.now(timezone.utc).isoformat()
                print_payload = json.dumps(response)
            elif request.get("operation") in ("probe","capture_raw"):
                from datetime import datetime, timezone
                raw_capture = request.get("operation") == "capture_raw"
                response = {"version":version,"account":account_summary(vnstock.status(),vnstock.usage()),
                            "market_request_attempted":False,
                            "market_request_succeeded":False}
                if raw_capture and not response["account"]["authenticated"]:
                    raise RuntimeError("AUTHENTICATION_REQUIRED")
                if raw_capture:
                    for window in ("minute","hour","day","month"):
                        limit = response["account"]["limits"].get(f"per_{window}")
                        used = response["account"]["used"].get(window)
                        if limit is None or used is None or used >= limit:
                            raise RuntimeError("QUOTA_UNAVAILABLE")
                if raw_capture or request.get("quote"):
                    try:
                        response["market_request_attempted"] = True
                        frame = Market().quote(request["symbols"],source="kbs")
                        response["received_at_utc"] = datetime.now(timezone.utc).isoformat()
                        response.update(quote_evidence(frame))
                        response["market_request_succeeded"] = True
                    except Exception as error:
                        response.update(error_summary(error))
                    response.setdefault("received_at_utc",datetime.now(timezone.utc).isoformat())
                response["account_after"] = account_summary(vnstock.status(),vnstock.usage())
                print_payload = json.dumps(response)
            else:
                # Guest trials are for initial inspection, never unattended collection.
                if vnstock.status().get("authenticated") is not True:
                    raise RuntimeError("AUTHENTICATION_REQUIRED")
                frame = Market().quote(request["symbols"], source="kbs")
                response = quote_evidence(frame)
                if response["missing_columns"]:
                    raise RuntimeError("MISSING_COLUMNS")
                print_payload = json.dumps({"rows":response["rows"],"version":version})
        print(print_payload)
    except Exception as error:
        # SDK exceptions/logs can contain request headers; do not echo them.
        response = error_summary(error)
        if str(error) in ("SDK_VERSION_UNVERIFIED", "MISSING_COLUMNS", "AUTHENTICATION_REQUIRED", "QUOTA_UNAVAILABLE"):
            response["error"] = str(error)
        print(json.dumps(response))


if __name__ == "__main__":
    main()
