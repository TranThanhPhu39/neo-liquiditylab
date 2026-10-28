"""GitHub REST client; credentials stay in memory and never enter logs/arguments."""
import json
import os
import re
import subprocess
import ssl
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler


class GitHubError(RuntimeError):
    def __init__(self,status):
        self.status=status
        super().__init__(f"GITHUB_HTTP_{status}")


def credential():
    token=os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        return token
    env={**os.environ,"GIT_TERMINAL_PROMPT":"0","GCM_INTERACTIVE":"never","GCM_GUI_PROMPT":"false"}
    result=subprocess.run(["git","credential","fill"],input="protocol=https\nhost=github.com\n\n",
        capture_output=True,text=True,timeout=20,env=env)
    if result.returncode:
        raise RuntimeError("GITHUB_AUTH_NOT_AVAILABLE")
    fields=dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
    if not fields.get("password"):
        raise RuntimeError("GITHUB_AUTH_NOT_AVAILABLE")
    return fields["password"]


class GitHubAPI:
    def __init__(self,token=None):
        self._token=token or credential()
        if os.name=="nt":
            import truststore
            self._tls=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        else:
            self._tls=ssl.create_default_context()
        self._opener=build_opener(HTTPSHandler(context=self._tls),SafeRedirect())

    def request(self,method,path,payload=None,*,data=None,content_type="application/json",binary=False,accept="application/vnd.github+json"):
        url=path if path.startswith("https://") else "https://api.github.com"+path
        if urlparse(url).hostname not in ("api.github.com","uploads.github.com"):
            raise ValueError("Unapproved GitHub API host")
        body=json.dumps(payload).encode() if payload is not None else data
        request=Request(url,data=body,method=method,headers={"Authorization":"Bearer "+self._token,
            "Accept":accept,"X-GitHub-Api-Version":"2022-11-28",
            "User-Agent":"NEO-LiquidityLab","Content-Type":content_type})
        try:
            with self._opener.open(request,timeout=90) as response:
                result=response.read()
        except HTTPError as error:
            raise GitHubError(error.code) from None
        return result if binary else json.loads(result) if result else None


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urlparse(newurl).scheme!="https":
            raise ValueError("HTTPS required")
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected and urlparse(req.full_url).hostname!=urlparse(newurl).hostname:
            redirected.remove_header("Authorization")
        return redirected


def repository_path(repository):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repository):
        raise ValueError("Invalid owner/repository")
    return "/repos/"+repository


if __name__=="__main__":
    user=GitHubAPI().request("GET","/user")
    print(json.dumps({"authenticated":True,"login":user["login"]}))
