"""Public DNS/TCP/TLS diagnostic. No API keys, request headers, or response bodies."""
from datetime import datetime,timezone
import json
from pathlib import Path
import socket
import ssl
import time

from src.data_providers.dnse_readonly import network_error_code

HOST="openapi.dnse.com.vn"


def diagnose():
    report={"provider":"dnse","purpose":"PUBLIC_NETWORK_DIAGNOSTIC",
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),"host":HOST,"attempts":[]}
    try:
        addresses=socket.getaddrinfo(HOST,443,type=socket.SOCK_STREAM)
    except OSError as error:
        return {**report,"status":"DNS_FAILED","error_code":network_error_code(error)}
    # One endpoint address is sufficient to isolate the observed failure stage.
    family,kind,protocol,_,address=addresses[0]
    result={"address":address[0],"family":"IPv6" if family==socket.AF_INET6 else "IPv4","stage":"TCP"}
    report["resolved_addresses"]=len({item[4][0] for item in addresses})
    started=time.monotonic()
    try:
        with socket.socket(family,kind,protocol) as connection:
            connection.settimeout(5)
            connection.connect(address)
            result.update(tcp_connected=True,stage="TLS")
            # Keep certificate chain and hostname verification enabled.
            context=ssl.create_default_context()
            with context.wrap_socket(connection,server_hostname=HOST) as secured:
                result.update(tls_verified=True,tls_version=secured.version(),stage="COMPLETE")
    except OSError as error:
        result["error_code"]=network_error_code(error)
    result["elapsed_seconds"]=round(time.monotonic()-started,3)
    report["attempts"].append(result)
    report["status"]="TCP_TLS_VERIFIED" if result.get("tls_verified") else "NETWORK_FAILED"
    return report


def main():
    report=diagnose()
    path=Path("report/dnse_network.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    main()
