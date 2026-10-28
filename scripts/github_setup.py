"""Publish reviewed source to the user's chosen repository; never stage .env or collected data."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

from config.settings import ROOT,get_vnstock_api_key
from scripts.github_api import GitHubAPI,GitHubError,repository_path

REPOSITORY="TranThanhPhu39/neo-liquiditylab"


def prepare_public_contract():
    original=json.loads((ROOT/"report/kbs_unit_evidence_2026-10-28.json").read_text(encoding="utf-8"))
    evidence={k:v for k,v in original.items() if k!="sdk_probe"}
    evidence["sdk_probe"]={k:v for k,v in original["sdk_probe"].items() if k not in ("account","account_after")}
    path=ROOT/"report/github_unit_evidence_2026-10-28.json"
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
    contract=json.loads((ROOT/"config/unit_contract.vnstock-kbs-5.2.1.json").read_text(encoding="utf-8"))
    contract["evidence"]={"source_url":"https://kbbuddywts.kbsec.com.vn/","local_path":"../report/"+path.name,
                          "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    (ROOT/"config/unit_contract.github-vnstock-kbs-5.2.1.json").write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding="utf-8")


def public_source():
    paths=[]
    for folder in ("src","scripts","config","tests","deploy","docs",".github/workflows"):
        paths.extend(p for p in (ROOT/folder).rglob("*") if p.is_file() and p.suffix in (".py",".ps1",".json",".yaml",".yml",".md",".txt",".service",".timer"))
    paths.extend(ROOT/name for name in ("README.md","pyproject.toml","requirements.txt","requirements-collector.txt","requirements-lock.txt","requirements-github.txt",".gitignore",".env.example"))
    paths.extend(p for p in (ROOT/"data/sample").glob("*") if p.is_file() and p.suffix in (".json",".csv"))
    paths.extend(ROOT/"report"/name for name in ("github_unit_evidence_2026-10-28.json","kbs_unit_review_2026-10-28.md"))
    result={}
    secrets=[value for value in (get_vnstock_api_key(),os.getenv("GH_TOKEN"),os.getenv("GITHUB_TOKEN"),
                                os.getenv("DNSE_API_KEY"),os.getenv("DNSE_API_SECRET")) if value]
    for path in sorted(set(paths)):
        relative=path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or path.is_symlink():
            continue
        content=path.read_text(encoding="utf-8-sig")
        # The public clone uses a unit evidence copy without account/quota diagnostics.
        if relative=="config/unit_contract.vnstock-kbs-5.2.1.json":
            content=(ROOT/"config/unit_contract.github-vnstock-kbs-5.2.1.json").read_text(encoding="utf-8")
        if any(secret in content for secret in secrets):
            raise ValueError("SECRET_DETECTED_IN_PUBLIC_SOURCE")
        if len(content.encode())>1024*1024:
            raise ValueError("UNEXPECTED_LARGE_SOURCE_FILE")
        result[relative]=content
    return result


def publish(files):
    api=GitHubAPI()
    user=api.request("GET","/user")
    if user["login"]!="TranThanhPhu39":
        raise ValueError("UNEXPECTED_GITHUB_ACCOUNT")
    base=repository_path(REPOSITORY)
    try:
        repo=api.request("GET",base)
        if not repo.get("description","").startswith("NEO LiquidityLab"):
            raise ValueError("EXISTING_REPOSITORY_REQUIRES_REVIEW")
    except GitHubError as error:
        if error.status!=404:
            raise
        repo=api.request("POST","/user/repos",{"name":"neo-liquiditylab","private":False,"auto_init":True,
            "description":"NEO LiquidityLab - Vietnamese equity liquidity collection and pre-trade simulation. NEO integration pending."})
    if repo["private"]:
        raise ValueError("REPOSITORY_VISIBILITY_DOES_NOT_MATCH_AUTHORIZATION")
    branch=repo["default_branch"]
    if branch!="main":
        raise ValueError("EXPECTED_MAIN_BRANCH")
    head=api.request("GET",base+"/git/ref/heads/main")["object"]["sha"]
    commit=api.request("GET",base+"/git/commits/"+head)
    tree=api.request("POST",base+"/git/trees",{"base_tree":commit["tree"]["sha"],
        "tree":[{"path":path,"mode":"100644","type":"blob","content":content} for path,content in files.items()]})
    new=api.request("POST",base+"/git/commits",{"message":"Add LiquidityLab collector, tests and guarded GitHub Actions workflows",
        "tree":tree["sha"],"parents":[head]})
    api.request("PATCH",base+"/git/refs/heads/main",{"sha":new["sha"],"force":False})
    return {"repository":REPOSITORY,"url":repo["html_url"],"commit":new["sha"],"source_files":len(files),
            "worker_variable_changed":False,"secrets_changed":False}


def protected_state_key():
    """Keep a recoverable local state-key backup encrypted to the Windows user."""
    path=ROOT/"data/control/github_state_key.dpapi"
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        mode="Unprotect"
        payload=base64.b64encode(path.read_bytes()).decode()
    else:
        mode="Protect"
        payload=base64.b64encode(os.urandom(32)).decode()
    command=("Add-Type -AssemblyName System.Security; $inputValue=[Console]::In.ReadToEnd(); "
        "$bytes=[Convert]::FromBase64String($inputValue); "
        "$result=[Security.Cryptography.ProtectedData]::"+mode+"($bytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "[Console]::Out.Write([Convert]::ToBase64String($result))")
    result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],input=payload,
        capture_output=True,text=True,timeout=20)
    if result.returncode:
        raise RuntimeError("STATE_KEY_DPAPI_FAILED")
    if mode=="Protect":
        path.write_bytes(base64.b64decode(result.stdout,validate=True))
        return payload
    return result.stdout


def set_secrets(include_vnstock=False):
    from nacl.public import PublicKey,SealedBox
    api=GitHubAPI()
    base=repository_path(REPOSITORY)
    public=api.request("GET",base+"/actions/secrets/public-key")
    cipher=SealedBox(PublicKey(base64.b64decode(public["key"])))
    values={"LIQUIDITYLAB_STATE_KEY":protected_state_key()}
    if include_vnstock:
        key=get_vnstock_api_key()
        if not key:
            raise RuntimeError("LOCAL_VNSTOCK_KEY_MISSING")
        values["VNSTOCK_API_KEY"]=key
    for name,value in values.items():
        encrypted=base64.b64encode(cipher.encrypt(value.encode())).decode()
        api.request("PUT",base+"/actions/secrets/"+name,{"encrypted_value":encrypted,"key_id":public["key_id"]})
    return {"repository":REPOSITORY,"secret_names_set":sorted(values),"values_logged":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=["prepare","publish","secrets","preflight","storage-test","scope-test","enable","disable","status"])
    parser.add_argument("--include-vnstock",action="store_true")
    args=parser.parse_args()
    if args.operation in ("prepare","publish"):
        prepare_public_contract()
        files=public_source()
        result={"source_files":len(files),"source_bytes":sum(len(x.encode()) for x in files.values()),
                "excluded":[".env","collected data","account quota diagnostics","SDK state","local control files"],
                "paths":list(files)} if args.operation=="prepare" else publish(files)
    elif args.operation=="secrets":
        result=set_secrets(args.include_vnstock)
    else:
        api=GitHubAPI()
        base=repository_path(REPOSITORY)
        if args.operation in ("preflight","storage-test","scope-test"):
            api.request("POST",base+"/actions/workflows/collect.yml/dispatches",{"ref":"main","inputs":{"mode":args.operation}})
            result={"dispatched_mode":args.operation}
        elif args.operation in ("enable","disable"):
            value="true" if args.operation=="enable" else "false"
            try:
                api.request("PATCH",base+"/actions/variables/LIQUIDITYLAB_WORKER_ENABLED",{"name":"LIQUIDITYLAB_WORKER_ENABLED","value":value})
            except GitHubError as error:
                if error.status!=404:
                    raise
                api.request("POST",base+"/actions/variables",{"name":"LIQUIDITYLAB_WORKER_ENABLED","value":value})
            result={"worker_enabled":value=="true"}
        else:
            runs=api.request("GET",base+"/actions/runs?per_page=6")
            result={"runs":[{k:r[k] for k in ("id","name","status","conclusion","event","html_url")} for r in runs["workflow_runs"]]}
            try:
                variable=api.request("GET",base+"/actions/variables/LIQUIDITYLAB_WORKER_ENABLED")
                result["worker_enabled"]=variable.get("value")=="true"
            except GitHubError as error:
                if error.status!=404:
                    raise
                result["worker_enabled"]=False
    (ROOT/"report/github").mkdir(parents=True,exist_ok=True)
    (ROOT/"report/github"/(args.operation+".json")).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":
    main()
