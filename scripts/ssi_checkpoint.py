"""Encrypted Parquet checkpoints, downloaded/decrypted/validated before success."""
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from scripts.github_api import GitHubAPI,GitHubError,repository_path,release_asset_bytes

CHUNK=16*1024*1024


def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
def sha(data):return hashlib.sha256(data).hexdigest()


def pack(run_folder,manifest,status,key,context):
    """Take only immutable committed parts referenced by one captured manifest."""
    if len(key)!=32:raise ValueError('SSI_STATE_KEY_REQUIRES_32_BYTES')
    bundle=io.BytesIO();files={}
    manifest=dict(manifest,state='CHECKPOINT_SNAPSHOT' if status.get('active') else status.get('state','STOPPED'))
    files['run/manifest.json']=encode(manifest);files['status.json']=encode(status)
    for item in manifest['files']:
        if not re.fullmatch(r'part-\d{8}\.parquet',item['path']):raise ValueError('SSI_UNSAFE_CHECKPOINT_PATH')
        path=Path(run_folder)/item['path']
        if path.is_symlink():raise ValueError('SSI_CHECKPOINT_SYMLINK')
        data=path.read_bytes()
        if len(data)!=item['bytes'] or sha(data)!=item['sha256']:raise ValueError('SSI_CHECKPOINT_SOURCE_CHANGED')
        files['run/'+item['path']]=data
    # No pending journal or credential/control files may enter the archive.
    with tarfile.open(fileobj=bundle,mode='w:gz',compresslevel=1) as archive:
        for name,data in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
    raw=bundle.getvalue();cipher=AESGCM(key);assets={};parts=[]
    for index,offset in enumerate(range(0,len(raw),CHUNK)):
        nonce=os.urandom(12);encrypted=nonce+cipher.encrypt(nonce,raw[offset:offset+CHUNK],(context+':'+str(index)).encode())
        name=f'part-{index:04d}.enc';assets[name]=encrypted;parts.append(dict(name=name,bytes=len(encrypted),sha256=sha(encrypted)))
    assets['manifest.json']=encode(dict(format='ssi-encrypted-parquet-v1',context=context,archive_sha256=sha(raw),parts=parts))
    return assets


def unpack(assets,key,context,*,max_bytes=512*1024*1024):
    outer=json.loads(assets['manifest.json'])
    if outer.get('format')!='ssi-encrypted-parquet-v1' or outer.get('context')!=context or not outer.get('parts'):
        raise ValueError('SSI_CHECKPOINT_CONTEXT_INVALID')
    if set(assets)!={'manifest.json',*(p['name'] for p in outer['parts'])}:raise ValueError('SSI_CHECKPOINT_ASSET_SET_INVALID')
    # Decrypted archive goes to a temporary stream, avoiding two full RAM copies.
    files={};size=0;archive_bytes=0;archive_hash=hashlib.sha256()
    with tempfile.TemporaryFile() as decrypted:
        for i,p in enumerate(outer['parts']):
            if p['name']!=f'part-{i:04d}.enc':raise ValueError('SSI_CHECKPOINT_PART_NAME_INVALID')
            data=assets[p['name']]
            if len(data)!=p['bytes'] or sha(data)!=p['sha256']:raise ValueError('SSI_CHECKPOINT_HASH_MISMATCH')
            block=AESGCM(key).decrypt(data[:12],data[12:],(context+':'+str(i)).encode());archive_bytes+=len(block)
            if archive_bytes>max_bytes:raise ValueError('SSI_CHECKPOINT_SIZE_LIMIT')
            archive_hash.update(block);decrypted.write(block);del block,data
        if archive_hash.hexdigest()!=outer['archive_sha256']:raise ValueError('SSI_CHECKPOINT_ARCHIVE_CHANGED')
        decrypted.seek(0)
        with tarfile.open(fileobj=decrypted,mode='r:gz') as archive:
            for m in archive:
                allowed=m.name in ('run/manifest.json','status.json') or re.fullmatch(r'run/part-\d{8}\.parquet',m.name)
                size+=m.size
                if not allowed or not m.isfile() or m.name in files or m.size<0 or size>max_bytes:
                    raise ValueError('SSI_UNSAFE_CHECKPOINT_ARCHIVE')
                files[m.name]=archive.extractfile(m).read()
    manifest=json.loads(files['run/manifest.json']);status=json.loads(files['status.json'])
    if manifest.get('format')!='liquiditylab-ssi-live-v1':raise ValueError('SSI_CHECKPOINT_RUN_FORMAT_INVALID')
    expected={'run/manifest.json','status.json',*('run/'+p['path'] for p in manifest['files'])}
    if set(files)!=expected:raise ValueError('SSI_CHECKPOINT_FILE_SET_INVALID')
    import pyarrow as pa
    import pyarrow.parquet as pq
    total=0
    for p in manifest['files']:
        data=files['run/'+p['path']]
        if len(data)!=p['bytes'] or sha(data)!=p['sha256']:raise ValueError('SSI_CHECKPOINT_PARQUET_CHANGED')
        rows=pq.ParquetFile(pa.BufferReader(data)).metadata.num_rows
        if rows!=p['rows'] or p['last']-p['first']+1!=rows:raise ValueError('SSI_CHECKPOINT_ROW_COUNT_INVALID')
        if p['first']!=total:raise ValueError('SSI_CHECKPOINT_ORDINAL_GAP')
        total+=rows
    if total!=manifest['last_ordinal']+1:raise ValueError('SSI_CHECKPOINT_TOTAL_INVALID')
    return files,dict(state='DOWNLOADED_DECRYPTED_PARQUET_VERIFIED',records=total,
                      quote_counts=manifest['quote_counts'],trade_counts=manifest['trade_counts'],run_id=manifest['run_id'])


class SSICheckpoints:
    def __init__(self,repository,key,*,api=None):
        self.repository=repository;self.base=repository_path(repository);self.key=key;self.api=api or GitHubAPI()
        self.created=[]

    def restore(self,tag,*,max_bytes=512*1024*1024):
        if not re.fullmatch(r'ssi-(?:ws|market)-[a-z0-9-]{10,150}',tag):raise ValueError('SSI_CHECKPOINT_TAG_INVALID')
        release=self.api.request('GET',self.base+'/releases/tags/'+quote(tag,safe=''))
        assets={};total=0
        for a in release['assets']:
            if a['name']!='manifest.json' and not re.fullmatch(r'part-\d{4}\.enc',a['name']):raise ValueError('SSI_UNEXPECTED_RELEASE_ASSET')
            if a['size']>CHUNK+4096:raise ValueError('SSI_OVERSIZED_RELEASE_ASSET')
            total+=a['size']
            if total>max_bytes:raise ValueError('SSI_CHECKPOINT_SIZE_LIMIT')
            assets[a['name']]=release_asset_bytes(self.api,self.repository,a,limit=CHUNK+4096)
        return unpack(assets,self.key,self.repository+':'+tag,max_bytes=max_bytes)

    def publish(self,service,session,run_id,attempt,*,final=False):
        if not re.fullmatch(r'[0-9]{1,24}',str(run_id)) or not re.fullmatch(r'[0-9]{1,8}',str(attempt)):
            raise ValueError('SSI_INVALID_GITHUB_RUN_ID')
        with service.lock:
            service.journal.flush()
            manifest=json.loads(json.dumps(service.journal.manifest));status=json.loads(json.dumps(service.status()));status.pop('csrf',None)
            folder=service.journal.folder
        market = manifest.get('capture_scope') == 'SSI_EQUITY_MARKET'
        prefix = 'ssi-market' if market else 'ssi-ws'
        clock = datetime.fromisoformat(manifest['created_at']) if market else datetime.now(timezone.utc)
        stamp=clock.strftime('%Y%m%d%H%M%S%f')
        tag=f'{prefix}-{manifest["day"]}-{session}-{stamp}-{run_id}-{attempt}'
        if market:
            try:
                existing = self.api.request('GET',self.base+'/releases/tags/'+quote(tag,safe=''))
            except GitHubError as error:
                if error.status != 404:
                    raise
            else:
                if existing.get('draft'):
                    raise ValueError('SSI_MARKET_SEGMENT_RELEASE_NOT_PUBLISHED')
                _,proof = self.restore(tag)
                if proof['run_id'] != manifest['run_id'] or proof['records'] != manifest['last_ordinal']+1:
                    raise ValueError('SSI_MARKET_SEGMENT_RELEASE_CONFLICT')
                return dict(tag=tag,encrypted_bytes=sum(a['size'] for a in existing['assets']),final=True,verification=proof)
        context=self.repository+':'+tag;assets=pack(folder,manifest,status,self.key,context)
        # Local decrypt verifies fidelity before creating any release.
        unpack(assets,self.key,context)
        release=self.api.request('POST',self.base+'/releases',dict(tag_name=tag,target_commitish='main',
            name='Encrypted SSI '+('market segment' if market else 'session' if final else 'temporary checkpoint')+' '+manifest['day']+' '+session,
            draft=True,prerelease=True,body='AES-GCM encrypted Parquet capture. No credentials or plaintext market payload. '+
                ('Source Windows local capture ' if session.endswith('-local') else 'Source Actions run ')+str(run_id)))
        upload=release['upload_url'].split('{')[0]
        for name,data in assets.items():
            self.api.request('POST',upload+'?name='+quote(name,safe=''),data=data,content_type='application/octet-stream')
        self.api.request('PATCH',self.base+'/releases/'+str(release['id']),{'draft':False})
        _,proof=self.restore(tag)
        if market:
            return dict(tag=tag,encrypted_bytes=sum(map(len,assets.values())),final=True,verification=proof)
        self.created.append(dict(tag=tag,id=release['id'],final=final))
        prune_errors=[]
        if final:
            # Only this worker's own superseded temporary snapshots; never old sessions.
            for old in self.created[:-1]:
                try:
                    self.api.request('DELETE',self.base+'/releases/'+str(old['id']))
                    self.api.request('DELETE',self.base+'/git/refs/tags/'+old['tag'])
                except Exception as error:prune_errors.append(type(error).__name__)
            self.created=self.created[-1:]
        return dict(tag=tag,encrypted_bytes=sum(map(len,assets.values())),final=final,verification=proof,prune_errors=prune_errors)
