"""Append journal + immutable Parquet batches. No full-history SQLite copy."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()


def atomic(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('wb') as stream:
        stream.write(encoded(value));stream.flush();os.fsync(stream.fileno())
    tmp.replace(path)


class LiveJournal:
    def __init__(self,root,symbols,*,folder=None,batch_size=1000,flush_seconds=10):
        self.root=Path(root).resolve();now=datetime.now(timezone.utc)
        self.run_id=uuid.uuid4().hex
        self.folder=Path(folder) if folder else self.root/'data/realtime/ssi'/now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()/self.run_id
        self.folder.mkdir(parents=True,exist_ok=True)
        self.manifest_path=self.folder/'manifest.json'
        if self.manifest_path.exists():
            self.manifest=json.loads(self.manifest_path.read_bytes());self.run_id=self.manifest['run_id']
        else:
            self.manifest=dict(format='liquiditylab-ssi-live-v1',run_id=self.run_id,day=self.folder.parent.name,
                symbols=list(symbols),created_at=now.isoformat(),state='RECORDING',files=[],last_ordinal=-1,
                quote_counts={s:0 for s in symbols},trade_counts={s:0 for s in symbols},
                unit_status='UNVERIFIED_SSI_QUOTE',clock_policy='RECEIVE_ORDER_WITH_ORIGINAL_SOURCE_CLOCK',
                provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE',price_unit='SSI_SOURCE_PRICE',volume_unit='SSI_SOURCE_QUANTITY')
            atomic(self.manifest_path,self.manifest)
        self.pending=self.folder/'pending.jsonl';self.buffer=[];self.batch_size=batch_size;self.flush_seconds=flush_seconds
        self.last_flush=time.monotonic();self.ordinal=self.manifest['last_ordinal']
        if self.pending.exists():
            lines=self.pending.read_bytes().splitlines(keepends=True)
            for index,line in enumerate(lines):
                try:r=json.loads(line)
                except (ValueError,UnicodeError):
                    self.manifest['recovery_gap']='INCOMPLETE_JOURNAL_TAIL'
                    with (self.folder/'recovery-tail.bin').open('ab') as quarantine:
                        quarantine.write(b''.join(lines[index:]));quarantine.flush();os.fsync(quarantine.fileno())
                    break
                if r['ordinal']>self.manifest['last_ordinal']:
                    self.buffer.append(r);self.ordinal=max(self.ordinal,r['ordinal'])
            if self.buffer:self.flush()
            else:
                with self.pending.open('wb') as stream:stream.flush();os.fsync(stream.fileno())
                atomic(self.manifest_path,self.manifest)
        if not hasattr(self,'handle'):self.handle=self.pending.open('ab')

    def append(self,record):
        self.ordinal+=1;record=dict(record,ordinal=self.ordinal)
        self.handle.write(encoded(record)+b'\n');self.handle.flush();os.fsync(self.handle.fileno())
        self.buffer.append(record)
        if len(self.buffer)>=self.batch_size:self.flush()
        return record

    def tick(self):
        if self.buffer and time.monotonic()-self.last_flush>=self.flush_seconds:self.flush()

    def flush(self):
        if not self.buffer:return
        import pyarrow as pa
        import pyarrow.parquet as pq
        rows=self.buffer
        schema=pa.schema([('ordinal',pa.int64()),('kind',pa.string()),('symbol',pa.string()),('quote_index',pa.int64()),
                          ('received_at',pa.timestamp('us',tz='UTC')),('record_json',pa.binary())])
        values=[dict(ordinal=r['ordinal'],kind=r['kind'],symbol=r['symbol'],quote_index=r.get('quote_index',-1),
                     received_at=datetime.fromisoformat(r['received_at']),record_json=encoded(r)) for r in rows]
        name=f'part-{len(self.manifest["files"]):08d}.parquet';path=self.folder/name;tmp=path.with_suffix('.tmp')
        pq.write_table(pa.Table.from_pylist(values,schema=schema),tmp,compression='zstd',compression_level=6)
        with tmp.open('r+b') as durable:os.fsync(durable.fileno())
        tmp.replace(path)
        entry=dict(path=name,rows=len(rows),first=rows[0]['ordinal'],last=rows[-1]['ordinal'],bytes=path.stat().st_size,
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.manifest['files'].append(entry);self.manifest['last_ordinal']=rows[-1]['ordinal']
        for r in rows:
            key='quote_counts' if r['kind']=='QUOTE' else 'trade_counts' if r['kind']=='TRADE' else None
            if key:self.manifest[key][r['symbol']]+=1
        atomic(self.manifest_path,self.manifest)
        if hasattr(self,'handle'):
            self.handle.close()
        with self.pending.open('wb') as durable:durable.flush();os.fsync(durable.fileno())
        self.handle=self.pending.open('ab')
        self.buffer=[];self.last_flush=time.monotonic()

    def close(self,state='STOPPED'):
        self.flush();self.handle.close()
        self.manifest.update(state=state,closed_at=datetime.now(timezone.utc).isoformat())
        atomic(self.manifest_path,self.manifest)


def run_folder(root,run_id):
    if not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id):raise ValueError('INVALID_SSI_RUN')
    base=Path(root)/'data/realtime/ssi'
    matches=list(base.glob('????-??-??/'+run_id+'/manifest.json'))
    if len(matches)!=1 or matches[0].is_symlink():raise ValueError('UNKNOWN_SSI_RUN')
    folder=matches[0].parent.resolve()
    if not folder.is_relative_to(base.resolve()):raise ValueError('INVALID_SSI_RUN_PATH')
    return folder


def read_records(folder,symbol,kind,*,start=0,limit=5000,begin=None,end=None):
    import pyarrow.dataset as ds
    manifest=json.loads((Path(folder)/'manifest.json').read_bytes())
    if symbol not in manifest['symbols'] or kind not in ('QUOTE','TRADE'):raise ValueError('INVALID_SSI_QUERY')
    if not 1<=limit<=10000:raise ValueError('INVALID_SSI_QUERY_LIMIT')
    result=[]
    for f in manifest['files']:
        if not re.fullmatch(r'part-\d{8}\.parquet',f['path']):raise ValueError('INVALID_SSI_PARTITION')
        path=Path(folder)/f['path']
        if path.is_symlink():raise ValueError('INVALID_SSI_PARTITION')
        condition=(ds.field('symbol')==symbol)&(ds.field('kind')==kind)
        if kind=='QUOTE':condition=condition&(ds.field('quote_index')>=start)
        if begin:condition=condition&(ds.field('received_at')>=datetime.fromisoformat(begin))
        if end:condition=condition&(ds.field('received_at')<=datetime.fromisoformat(end))
        table=ds.dataset(path,format='parquet').to_table(columns=['record_json'],filter=condition)
        for r in table.to_pylist():
            result.append(json.loads(r['record_json']))
            if len(result)>=limit:return result
    return result
