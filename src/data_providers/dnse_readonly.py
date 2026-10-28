"""Read-only DNSE quotes and equity reference data; no account or order routes."""
import base64
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
import re
import socket
import ssl
import time
from urllib.error import HTTPError,URLError
from urllib.parse import quote,urlencode
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler
from uuid import uuid4

from src.data_providers.base import NotConfigured,ProviderError

BASE_URL="https://openapi.dnse.com.vn"
API_VERSION="2026-07-23"


class DNSEHTTPError(ProviderError):
    def __init__(self,status):
        self.status=status
        super().__init__(f"DNSE_HTTP_{status}")


def network_error_code(error):
    reason=error.reason if isinstance(error,URLError) else error
    if isinstance(reason,ssl.SSLCertVerificationError):
        return "DNSE_TLS_VERIFICATION_FAILED"
    if isinstance(reason,socket.gaierror):
        return "DNSE_DNS_ERROR"
    if isinstance(reason,TimeoutError):
        return "DNSE_TIMEOUT"
    if isinstance(reason,ssl.SSLError):
        return "DNSE_TLS_ERROR"
    if isinstance(reason,ConnectionError):
        return "DNSE_CONNECTION_ERROR"
    return "DNSE_NETWORK_ERROR"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def signed_headers(api_key,api_secret,path,*,date_value=None,nonce=None):
    if any(not value or '\r' in value or '\n' in value for value in (api_key,api_secret)):
        raise NotConfigured("DNSE_CREDENTIALS_MISSING_OR_INVALID")
    date_value=date_value or datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    nonce=nonce or uuid4().hex
    canonical=f"(request-target): get {path}\ndate: {date_value}\nnonce: {nonce}"
    digest=hmac.new(api_secret.encode(),canonical.encode(),hashlib.sha256).digest()
    signature=quote(base64.b64encode(digest).decode(),safe="")
    value=(f'Signature keyId="{api_key}",algorithm="hmac-sha256",headers="(request-target) date",'
           f'signature="{signature}",nonce="{nonce}"')
    return {"Date":date_value,"X-Signature":value,"x-api-key":api_key,"version":API_VERSION,"Accept":"application/json"}


def redact_payload(value,credentials):
    if isinstance(value,dict):
        return {key:redact_payload(item,credentials) for key,item in value.items()
                if not re.search(r"api.?key|secret|password|authorization|account|access.?token|refresh.?token",key,re.I)}
    if isinstance(value,list):
        return [redact_payload(item,credentials) for item in value]
    if isinstance(value,str):
        for secret in credentials:
            if secret:
                value=value.replace(secret,"[REDACTED]")
    return value


class DNSEReadOnly:
    def __init__(self,api_key=None,api_secret=None,*,opener=None,min_interval=2):
        self._key=api_key or os.getenv("DNSE_API_KEY")
        self._secret=api_secret or os.getenv("DNSE_API_SECRET")
        if not self._key or not self._secret:
            raise NotConfigured("Cần lưu DNSE_API_KEY và DNSE_API_SECRET cục bộ; không gửi vào chat")
        if opener is None:
            if os.name=="nt":
                try:
                    import truststore
                    context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                except ImportError:
                    context=ssl.create_default_context()
            else:
                context=ssl.create_default_context()
            opener=build_opener(HTTPSHandler(context=context),NoRedirect())
        self._opener=opener
        self._last=None
        # Quotes: documented 10,000/hour. 0.5s permits at most 7,200/hour.
        self._interval=max(0.5,min_interval)

    def get(self,path,params=None):
        instruments=path=="/instruments"
        if not instruments and not re.fullmatch(r"/price/[A-Z0-9]{1,12}/quotes(?:/latest)?",path):
            raise ValueError("DNSE_ROUTE_NOT_ALLOWED")
        allowed={"symbol","marketId","securityGroupId","indexName","limit","page"} if instruments else {
            "boardId","from","to","limit","order","nextPageToken"}
        if set(params or {})-allowed:
            raise ValueError("DNSE_QUERY_NOT_ALLOWED")
        if self._last is not None:
            spacing=max(4,self._interval) if instruments else self._interval
            time.sleep(max(0,spacing-(time.monotonic()-self._last)))
        headers=signed_headers(self._key,self._secret,path)
        url=BASE_URL+path+("?"+urlencode(params) if params else "")
        request=Request(url,method="GET",headers=headers)
        self._last=time.monotonic()
        try:
            with self._opener.open(request,timeout=30) as response:
                raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:
                    raise ProviderError("DNSE_RESPONSE_TOO_LARGE")
                received=datetime.now(timezone.utc).isoformat()
                response_headers=getattr(response,"headers",{})
                limits={name:response_headers.get(name) for name in
                        ("X-RateLimit-Limit","X-RateLimit-Remaining","X-RateLimit-Reset")
                        if re.fullmatch(r"[0-9]{1,20}",str(response_headers.get(name,"")))}
        except HTTPError as error:
            raise DNSEHTTPError(error.code) from None
        except (URLError,TimeoutError,ConnectionError,ssl.SSLError) as error:
            raise ProviderError(network_error_code(error)) from None
        try:
            payload=json.loads(raw)
        except (ValueError,UnicodeError):
            raise ProviderError("DNSE_INVALID_JSON") from None
        return {"provider":"dnse","method":"GET","path":path,"received_at_utc":received,
                "api_version":API_VERSION,"units_verified":False,
                "rate_limit_headers":limits,
                "payload":redact_payload(payload,(self._key,self._secret))}
