"""Authenticated, sanitized SDK requests for collection setup."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import re

from config.settings import ROOT


class SDKRequestError(RuntimeError):
    def __init__(self,response):
        self.category=response.get("error")
        self.code=response.get("error_code")
        if not isinstance(self.category,str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}",self.category):
            self.category="UNKNOWN_SDK_ERROR"
        if not isinstance(self.code,str) or not re.fullmatch(r"[A-Z0-9_]{1,100}",self.code):
            self.code=None
        super().__init__("SDK_REQUEST_FAILED")


def sdk_request(operation, *, symbols=None, timeout=60):
    request = {"operation":operation}
    if symbols is not None:
        request.update(symbols=list(symbols),quote=True)
    result = subprocess.run([sys.executable,"-m","src.data_providers.vnstock_worker"],
        input=json.dumps(request),capture_output=True,text=True,encoding="utf-8",timeout=timeout,cwd=ROOT)
    try:
        response = json.loads(result.stdout)
    except (ValueError,TypeError):
        raise RuntimeError("INVALID_WORKER_RESPONSE") from None
    if result.returncode or response.get("error"):
        raise SDKRequestError(response)
    return response


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=["universe","market_status","policy"])
    parser.add_argument("--output",required=True)
    args = parser.parse_args()
    response = sdk_request(args.operation)
    path = Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"operation":args.operation,"rows":len(response.get("rows",[])),"output":str(path),
                      "columns":response.get("columns"),"rule":response.get("rule")}))


if __name__=="__main__":
    main()
