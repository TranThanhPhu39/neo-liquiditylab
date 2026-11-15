"""Bounded encrypted cloud seed/recovery. Public Releases hold ciphertext only."""
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import tarfile
import tempfile
import threading
import uuid
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from scripts.github_api import GitHubAPI,repository_path,release_asset_bytes
from scripts.ssi_checkpoint import SSICheckpoints
from scripts.restore_ssi_checkpoint import install

REPOSITORY='TranThanhPhu39/neo-liquiditylab'
CHUNK=16*1024*1024
MAX_TOTAL=768*1024*1024


def digest(data):return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p=PurePosixPath(name)
    return (not p.is_absolute() and '..' not in p.parts and '\\' not in name and
        (name=='seed-manifest.json' or
         (name.startswith(('data/compact/','data/exports/orderbook-monitor/')) and p.suffix in ('.json','.parquet')) or
         name in ('data/processed/dnse/manifest.json','report/dnse_normalization_audit.json')))


def encrypt_seed(folder,key,tag,output):
    folder=Path(folder).resolve();output=Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((folder/'seed-manifest.json').read_bytes());names=['seed-manifest.json']+[f['path'] for f in manifest['files']]
    if len(key)!=32 or not all(safe_name(n) for n in names) or len(set(names))!=len(names):raise ValueError('WEB_SEED_INVALID')
    tarpath=output/'private-seed.tar.gz'
    with tarfile.open(tarpath,'w:gz',compresslevel=1) as tar:
        for name in names:
            p=folder/name
            if p.is_symlink() or not p.resolve().is_relative_to(folder):raise ValueError('WEB_SEED_UNSAFE_PATH')
            tar.add(p,arcname=name,recursive=False)
    context=REPOSITORY+':'+tag;parts=[];archive_hash=hashlib.sha256()
    with tarpath.open('rb') as stream:
        for i,block in enumerate(iter(lambda:stream.read(CHUNK),b'')):
            archive_hash.update(block);nonce=os.urandom(12)
            encrypted=nonce+AESGCM(key).encrypt(nonce,block,(context+':'+str(i)).encode())
            name=f'part-{i:04d}.enc';(output/name).write_bytes(encrypted)
            parts.append(dict(name=name,bytes=len(encrypted),sha256=digest(encrypted)))
    outer=dict(format='liquiditylab-encrypted-web-seed-v1',context=context,archive_sha256=archive_hash.hexdigest(),parts=parts)
    (output/'manifest.json').write_text(json.dumps(outer),encoding='utf-8')
    return ['manifest.json']+[p['name'] for p in parts]


def publish_seed(folder,key,api=None):
    api=api or GitHubAPI();tag='monitor-web-seed-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:8]
    output=Path(folder).parent/(Path(folder).name+'-encrypted');names=encrypt_seed(folder,key,tag,output)
    base=repository_path(REPOSITORY)
    release=api.request('POST',base+'/releases',dict(tag_name=tag,target_commitish='main',name='Private encrypted monitor web seed',
        draft=True,prerelease=True,body='Encrypted private dashboard seed. No plaintext market data or credentials.'))
    for name in names:
        api.request('POST',release['upload_url'].split('{')[0]+'?name='+quote(name,safe=''),data=(output/name).read_bytes(),content_type='application/octet-stream')
    api.request('PATCH',base+'/releases/'+str(release['id']),dict(draft=False))
    return dict(tag=tag,bytes=sum((output/n).stat().st_size for n in names),files=len(names))


def restore_seed(root,key,tag,api=None):
    if not re.fullmatch(r'monitor-web-seed-[a-z0-9-]{10,80}',tag):raise ValueError('WEB_SEED_TAG')
    api=api or GitHubAPI(token='');base=repository_path(REPOSITORY)
    release=api.request('GET',base+'/releases/tags/'+tag);assets={a['name']:a for a in release['assets']}
    if any(n!='manifest.json' and not re.fullmatch(r'part-\d{4}\.enc',n) for n in assets):raise ValueError('WEB_SEED_ASSETS')
    def download(name):
        a=assets[name]
        if a['size']>CHUNK+4096:raise ValueError('WEB_SEED_ASSET_LIMIT')
        return release_asset_bytes(api,REPOSITORY,a,limit=CHUNK+4096)
    outer=json.loads(download('manifest.json'));context=REPOSITORY+':'+tag
    if outer.get('format')!='liquiditylab-encrypted-web-seed-v1' or outer.get('context')!=context:raise ValueError('WEB_SEED_CONTEXT')
    if set(assets)!={'manifest.json',*(p['name'] for p in outer['parts'])}:raise ValueError('WEB_SEED_ASSET_SET')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='web-seed-',dir=root) as tmp:
        stage=Path(tmp);archive=stage/'seed.tar.gz';h=hashlib.sha256();size=0
        if not stage.resolve().is_relative_to(root):raise ValueError('WEB_SEED_UNSAFE_STAGE')
        with archive.open('wb') as stream:
            for i,p in enumerate(outer['parts']):
                if p['name']!=f'part-{i:04d}.enc':raise ValueError('WEB_SEED_PART_ORDER')
                encrypted=download(p['name'])
                if len(encrypted)!=p['bytes'] or digest(encrypted)!=p['sha256']:raise ValueError('WEB_SEED_HASH')
                block=AESGCM(key).decrypt(encrypted[:12],encrypted[12:],(context+':'+str(i)).encode());size+=len(block)
                if size>MAX_TOTAL:raise ValueError('WEB_SEED_SIZE')
                h.update(block);stream.write(block)
        if h.hexdigest()!=outer['archive_sha256']:raise ValueError('WEB_SEED_ARCHIVE_HASH')
        names=set();total=0
        with tarfile.open(archive,'r:gz') as tar:
            for m in tar:
                total+=m.size
                if not m.isfile() or not safe_name(m.name) or m.name in names or total>MAX_TOTAL or m.size>96*1024*1024:
                    raise ValueError('WEB_SEED_UNSAFE_ARCHIVE')
                names.add(m.name)
                if len(names)>2048:raise ValueError('WEB_SEED_FILE_LIMIT')
                p=stage/'files'/m.name;p.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(m) as src,p.open('wb') as dst:
                    for block in iter(lambda:src.read(1024*1024),b''):dst.write(block)
        manifest=json.loads((stage/'files/seed-manifest.json').read_bytes())
        if manifest.get('format')!='liquiditylab-private-web-seed-v1' or names!={'seed-manifest.json',*(p['path'] for p in manifest['files'])}:
            raise ValueError('WEB_SEED_FILE_SET')
        for f in manifest['files']:
            p=stage/'files'/f['path'];h=hashlib.sha256()
            with p.open('rb') as src:
                for block in iter(lambda:src.read(1024*1024),b''):h.update(block)
            if p.stat().st_size!=f['bytes'] or h.hexdigest()!=f['sha256']:raise ValueError('WEB_SEED_FILE_HASH')
        # All names/hashes have passed before the first destination write.
        for f in sorted(manifest['files'],key=lambda x:x['path'].endswith('index.json')):
            target=(root/f['path']).resolve()
            if not target.is_relative_to(root):raise ValueError('WEB_SEED_UNSAFE_DESTINATION')
            target.parent.mkdir(parents=True,exist_ok=True);(stage/'files'/f['path']).replace(target)
        return dict(state='DECRYPTED_AND_VERIFIED',files=len(manifest['files']),datasets=manifest.get('datasets'),bytes=total)


class CloudRecovery:
    def __init__(self,root,key,seed_tag=None,*,api=None,live=None,on_seed=None):
        self.root=Path(root);self.key=key;self.seed_tag=seed_tag;self.api=api or GitHubAPI(token='');self.live=live
        self.state='NOT_STARTED';self.seed_ready=not bool(seed_tag);self.last_error=None;self.closed=threading.Event();self.thread=None;self.known_tags=set();self.on_seed=on_seed

    def start(self):
        def worker():
            while not self.closed.is_set():
                try:
                    self.state='RECOVERING'
                    if not self.seed_ready:
                        restore_seed(self.root,self.key,self.seed_tag,self.api)
                        if self.on_seed:self.on_seed()
                        self.seed_ready=True
                    releases=self.api.request('GET',repository_path(REPOSITORY)+'/releases?per_page=100')
                    for r in [x for x in releases if x['tag_name'].startswith('ssi-ws-')][:6]:
                        if r['tag_name'] in self.known_tags:continue
                        with self.live.lock if self.live else threading.RLock():
                            active=self.live.journal.run_id if self.live and self.live.journal and self.live.status()['active'] else None
                        files,proof=SSICheckpoints(REPOSITORY,self.key,api=self.api).restore(r['tag_name'],max_bytes=64*1024*1024)
                        if proof['run_id']==active:continue
                        with self.live.lock if self.live else threading.RLock():
                            existing=list((self.root/'data/realtime/ssi').glob('????-??-??/'+proof['run_id']+'/manifest.json'))
                            if existing:
                                old=json.loads(existing[0].read_bytes());new=json.loads(files['run/manifest.json'])
                                if old['last_ordinal']>=new['last_ordinal']:
                                    self.known_tags.add(r['tag_name']);continue
                                if self.live and self.live.journal and self.live.journal.run_id==proof['run_id']:
                                    if self.live.status()['active']:continue
                                    self.live.journal.close();self.live.journal=None
                                previous=existing[0].parent;backup=self.root/'data/control'/('relay-replaced-'+uuid.uuid4().hex)
                                if not previous.resolve().is_relative_to(self.root.resolve()) or not backup.resolve().is_relative_to(self.root.resolve()):
                                    raise ValueError('UNSAFE_RECOVERY_DESTINATION')
                                backup.parent.mkdir(parents=True,exist_ok=True);previous.rename(backup)
                                try:install(files,root=self.root)
                                except Exception:backup.rename(previous);raise
                                # The verified source archive now supersedes this relay copy.
                                if backup.resolve().is_relative_to((self.root/'data/control').resolve()) and backup.name.startswith('relay-replaced-'):
                                    shutil.rmtree(backup)
                            else:install(files,root=self.root)
                            self.known_tags.add(r['tag_name'])
                    self.state='READY';self.last_error=None
                except Exception as error:self.state='RECOVERY_RETRY';self.last_error=type(error).__name__
                self.closed.wait(900)
        self.thread=threading.Thread(target=worker,daemon=True,name='monitor-cloud-recovery');self.thread.start()

    def close(self):self.closed.set()
