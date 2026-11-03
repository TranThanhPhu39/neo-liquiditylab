"""Encrypted daily SQLite snapshots in immutable GitHub Releases; no SDK credential state."""
import base64
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile
import sqlite3
import uuid
from contextlib import closing
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scripts.github_api import GitHubAPI,repository_path

CHUNK_SIZE=16*1024*1024
ALLOWED={"snapshot.db","status.json","audit.json","universe.json"}


def backup_sqlite(source, target):
    """Copy any existing SQLite schema without opening it through a domain Storage class."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise FileExistsError('CHECKPOINT_WOULD_OVERWRITE_DATABASE')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name+'.pending-'+uuid.uuid4().hex)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=30)) as incoming:
            with closing(sqlite3.connect(temporary)) as outgoing:
                incoming.backup(outgoing)
                if outgoing.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or outgoing.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('CHECKPOINT_DATABASE_INTEGRITY_FAILED')
        if target.exists():
            raise FileExistsError('CHECKPOINT_WOULD_OVERWRITE_DATABASE')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def state_key(encoded):
    try:
        key=base64.b64decode(encoded,validate=True)
    except Exception:
        raise ValueError("INVALID_STATE_KEY") from None
    if len(key)!=32:
        raise ValueError("STATE_KEY_REQUIRES_32_BYTES")
    return key


def pack_state(files,folder,key,context):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    if not files or not set(files)<=ALLOWED:
        raise ValueError("UNAPPROVED_CHECKPOINT_FILE")
    archive=folder/"state.tar.gz"
    with tarfile.open(archive,"w:gz") as tar:
        for name,path in sorted(files.items()):
            tar.add(path,arcname=name,recursive=False)
    parts=[]
    cipher=AESGCM(key)
    import os
    with archive.open("rb") as source:
        for index in range(10000):
            block=source.read(CHUNK_SIZE)
            if not block:
                break
            nonce=os.urandom(12)
            encrypted=nonce+cipher.encrypt(nonce,block,f"{context}:{index}".encode())
            target=folder/f"part-{index:04d}.enc"
            target.write_bytes(encrypted)
            parts.append({"name":target.name,"bytes":len(encrypted),"sha256":hashlib.sha256(encrypted).hexdigest()})
    manifest={"version":1,"context":context,"parts":parts,
              "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()}
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest


def unpack_state(folder,target,key,context):
    folder,target=Path(folder),Path(target)
    manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version")!=1 or manifest.get("context")!=context or not manifest.get("parts"):
        raise ValueError("INVALID_CHECKPOINT_MANIFEST")
    archive=folder/"restored.tar.gz"
    with archive.open("wb") as stream:
        for index,part in enumerate(manifest["parts"]):
            if part["name"]!=f"part-{index:04d}.enc":
                raise ValueError("INVALID_PART_NAME")
            encrypted=(folder/part["name"]).read_bytes()
            if len(encrypted)!=part["bytes"] or hashlib.sha256(encrypted).hexdigest()!=part["sha256"]:
                raise ValueError("CHECKPOINT_HASH_MISMATCH")
            stream.write(AESGCM(key).decrypt(encrypted[:12],encrypted[12:],f"{context}:{index}".encode()))
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest["archive_sha256"]:
        raise ValueError("ARCHIVE_HASH_MISMATCH")
    target.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,"r:gz") as tar:
        seen=set()
        for member in tar:
            if member.name not in ALLOWED or member.name in seen or not member.isfile() or member.size>2*1024**3:
                raise ValueError("UNSAFE_ARCHIVE_ENTRY")
            seen.add(member.name)
            with tar.extractfile(member) as source,(target/member.name).open("xb") as dest:
                import shutil
                shutil.copyfileobj(source,dest)
    if "snapshot.db" not in seen:
        raise ValueError("MISSING_DATABASE")
    return seen


class ReleaseStore:
    def __init__(self,repository,key,api=None,namespace='market-state'):
        if not re.fullmatch('[a-z][a-z0-9-]{1,40}', namespace):
            raise ValueError('INVALID_CHECKPOINT_NAMESPACE')
        self.repository=repository
        self.base=repository_path(repository)
        self.key=key
        self.api=api or GitHubAPI()
        self.namespace=namespace

    def latest(self,day):
        if not re.fullmatch('[a-z0-9][a-z0-9-]{1,60}', day):
            raise ValueError('INVALID_CHECKPOINT_PARTITION')
        refs=self.api.request("GET",self.base+"/git/matching-refs/tags/"+self.namespace+"-"+day+"-")
        tags=sorted(ref["ref"].removeprefix("refs/tags/") for ref in refs)
        for tag in reversed(tags):
            release=self.api.request("GET",self.base+"/releases/tags/"+quote(tag,safe=""))
            if not release.get("draft"):
                return release
        return None

    def restore(self,day,database,report_folder):
        release=self.latest(day)
        if release is None:
            return None
        tag=release["tag_name"]
        context=self.repository+":"+tag
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for asset in release["assets"]:
                name=asset["name"]
                if name!="manifest.json" and not re.fullmatch(r"part-[0-9]{4}\.enc",name):
                    raise ValueError("UNEXPECTED_RELEASE_ASSET")
                if asset["size"]>CHUNK_SIZE+4096:
                    raise ValueError("OVERSIZED_RELEASE_ASSET")
                data=self.api.request("GET",self.base+f"/releases/assets/{asset['id']}",binary=True,accept="application/octet-stream")
                (folder/name).write_bytes(data)
            extracted=folder/"extracted"
            names=unpack_state(folder,extracted,self.key,context)
            backup_sqlite(extracted/"snapshot.db",database)
            report_folder=Path(report_folder)
            report_folder.mkdir(parents=True,exist_ok=True)
            for name in names-{"snapshot.db"}:
                target=report_folder/name
                if target.exists():
                    raise FileExistsError("RESTORE_WOULD_OVERWRITE_REPORT")
                target.write_bytes((extracted/name).read_bytes())
        return tag

    def publish(self,day,database,report_folder,run_id,attempt):
        stamp=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        if not re.fullmatch('[a-z0-9][a-z0-9-]{1,60}', day):
            raise ValueError('INVALID_CHECKPOINT_PARTITION')
        tag=f"{self.namespace}-{day}-{stamp}-{run_id}-{attempt}"
        context=self.repository+":"+tag
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            snapshot=folder/"snapshot.db"
            backup_sqlite(database,snapshot)
            files={"snapshot.db":snapshot}
            for name in ("status.json","audit.json","universe.json"):
                path=Path(report_folder)/name
                if path.is_file():
                    files[name]=path
            encrypted=folder/"encrypted"
            manifest=pack_state(files,encrypted,self.key,context)
            release=self.api.request("POST",self.base+"/releases",{
                "tag_name":tag,"target_commitish":"main","name":f"Encrypted {'synthetic storage test' if day=='storage-probe' else 'market checkpoint '+day} {stamp}",
                "draft":True,"prerelease":True,"body":"Encrypted SQLite and operational reports. No API keys or SDK authentication state. Source run "+str(run_id)})
            upload=release["upload_url"].split("{")[0]
            for name in [p["name"] for p in manifest["parts"]]+["manifest.json"]:
                self.api.request("POST",upload+"?name="+quote(name),data=(encrypted/name).read_bytes(),content_type="application/octet-stream")
            self.api.request("PATCH",self.base+f"/releases/{release['id']}",{"draft":False})
        return tag
