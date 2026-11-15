"""Authenticated transport of existing SSI observations; never calls a broker."""
from collections import deque
from datetime import datetime,timezone
import gzip
import json
import os
from pathlib import Path
import re
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPSHandler

from src.data_pipeline.ssi_live_store import LiveJournal,atomic
from src.data_providers.ssi_readonly import NoRedirect
from src.data_providers.ssi_stream import SYMBOLS
from src.monitoring.ssi_live import SSILive

MAX_RECORDS=256
MAX_BODY=2*1024*1024


def public_contract(value):
    allowed=('status','symbols','hash','trade_status','trade_symbols','price_multiplier','volume_multiplier',
             'trade_price_multiplier','trade_volume_multiplier','quote_semantics')
    return {k:value[k] for k in allowed if k in value}


def validate(packet):
    if not isinstance(packet,dict) or set(packet)!={'manifest','status','records'}:raise ValueError('WEB_RELAY_SCHEMA')
    m=packet['manifest'];rows=packet['records']
    if not re.fullmatch('[a-f0-9]{32}',m.get('run_id','')) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',m.get('day','')):
        raise ValueError('WEB_RELAY_ID')
    if not m.get('symbols') or len(set(m['symbols']))!=len(m['symbols']) or any(s not in SYMBOLS for s in m['symbols']):
        raise ValueError('WEB_RELAY_SYMBOLS')
    if not isinstance(rows,list) or len(rows)>MAX_RECORDS:raise ValueError('WEB_RELAY_LIMIT')
    def inspect(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if re.search(r'password|api.?key|api.?secret|authorization|access.?token|account',k,re.I):raise ValueError('WEB_RELAY_PRIVATE_FIELD')
                inspect(v)
        elif isinstance(value,list):
            for v in value:inspect(v)
    inspect(packet)
    for r in rows:
        kind=r.get('kind');symbol=r.get('symbol')
        if kind not in ('QUOTE','TRADE','MARKET','SYSTEM','QUARANTINE') or symbol not in ('',*m['symbols']):raise ValueError('WEB_RELAY_RECORD_SCOPE')
        if kind in ('QUOTE','TRADE') and symbol not in m['symbols']:raise ValueError('WEB_RELAY_RECORD_SCOPE')
        if type(r.get('ordinal')) is not int or not 0<=r['ordinal']<2_000_010:raise ValueError('WEB_RELAY_ORDINAL')
        if datetime.fromisoformat(r['received_at']).tzinfo is None:raise ValueError('WEB_RELAY_CLOCK')
        if kind=='QUOTE' and (r.get('frame',{}).get('index')!=r.get('quote_index') or not isinstance(r.get('canonical'),dict)):
            raise ValueError('WEB_RELAY_QUOTE')
        if kind=='TRADE' and r.get('trade',{}).get('capture_ordinal')!=r['ordinal']:raise ValueError('WEB_RELAY_TRADE')
    if any(b['ordinal']!=a['ordinal']+1 for a,b in zip(rows,rows[1:])):raise ValueError('WEB_RELAY_SEQUENCE')


class RelayLive(SSILive):
    """Same replay API as local SSI; the GitHub collector owns collection controls."""
    def __init__(self,root):
        super().__init__(root);self.delivery_at=None;self.upstream={};self.state='WAITING_FOR_COLLECTOR'

    def apply(self,packet):
        validate(packet);m=packet['manifest'];rows=packet['records']
        with self.lock:
            if not self.journal or self.journal.run_id!=m['run_id']:
                if self.journal:self.journal.close('WEB_RELAY_PAUSED')
                folder=self.root/'data/realtime/ssi'/m['day']/m['run_id']
                journal=LiveJournal(self.root,m['symbols'],folder=folder,flush_seconds=30)
                self.journal=journal;journal.run_id=m['run_id']
                journal.manifest.update(run_id=m['run_id'],created_at=m['created_at'],unit_contract=public_contract(m.get('unit_contract',{})),
                    normalization_version=m.get('normalization_version'),unit_verification_origin='UPSTREAM_COLLECTOR')
                self.contract=dict(m.get('unit_contract') or self.contract)
                self.frames={s:deque(maxlen=15000) for s in SYMBOLS};self.trades={s:deque(maxlen=30000) for s in SYMBOLS}
                self.patches={s:[] for s in SYMBOLS};self.counts={s:0 for s in SYMBOLS};self.trade_counts={s:0 for s in SYMBOLS}
                self.quote_total=self.trade_total=0
                if journal.manifest['last_ordinal']>=0:
                    import pyarrow.parquet as pq
                    for part in journal.manifest['files']:
                        for batch in pq.ParquetFile(folder/part['path']).iter_batches(batch_size=256,columns=['record_json']):
                            for row in batch.to_pylist():
                                r=json.loads(row['record_json']);code=r['symbol']
                                if r['kind']=='QUOTE':
                                    self.frames[code].append(r['frame']);self.patches[code].extend(r.get('patches',[]))
                                    self.counts[code]+=1;self.quote_total+=1;self.last_quote=r['received_at']
                                elif r['kind']=='TRADE':
                                    self.trades[code].append(r['trade']);self.trade_counts[code]+=1;self.trade_total+=1
                                elif r['kind']=='SYSTEM':
                                    for code,patches in r.get('patches',{}).items():self.patches[code].extend(patches)
                                self.last_received=r['received_at']
                atomic(journal.manifest_path,journal.manifest)
            expected=self.journal.ordinal+1
            # A restarted receiver requests a replay from ordinal zero. No silent hole.
            if rows and rows[0]['ordinal']>expected:return dict(next_ordinal=expected,resync=True)
            for r in rows:
                if r['ordinal']<expected:continue
                if r['ordinal']!=expected:raise ValueError('WEB_RELAY_SEQUENCE')
                symbol=r['symbol'];kind=r['kind']
                if kind=='QUOTE':
                    if r['quote_index']!=self.counts[symbol]:raise ValueError('WEB_RELAY_QUOTE_INDEX')
                    self.frames[symbol].append(r['frame']);self.patches[symbol].extend(r.get('patches',[]))
                    self.counts[symbol]+=1;self.quote_total+=1;self.last_quote=r['received_at']
                elif kind=='TRADE':
                    self.trades[symbol].append(r['trade']);self.trade_counts[symbol]+=1;self.trade_total+=1
                elif kind=='SYSTEM':
                    for code,patches in r.get('patches',{}).items():
                        if code not in SYMBOLS:raise ValueError('WEB_RELAY_PATCH_SCOPE')
                        self.patches[code].extend(patches)
                self.journal.append(r);self.last_received=r['received_at'];expected+=1
            self.journal.tick();self.delivery_at=datetime.now(timezone.utc)
            self.upstream=packet['status'];self.state=self.upstream.get('state','RECEIVING')
            self.flags=self.upstream.get('source_session_flags',{});self.gaps=self.upstream.get('gaps',0)
            self.rejected=self.upstream.get('rejected',0);self.connection=self.upstream.get('connection',0)
            self.reason=self.upstream.get('reason');self.first_connected_at=self.upstream.get('first_connected_at')
            if not self.upstream.get('active'):self.journal.flush()
            return dict(next_ordinal=expected)

    def status(self):
        r=super().status();age=(datetime.now(timezone.utc)-self.delivery_at).total_seconds() if self.delivery_at else None
        r.update(hosted=True,collection_control='GITHUB_SCHEDULE',delivery_age_seconds=age,
            active=bool(age is not None and age<=20 and self.upstream.get('active')),
            stale=bool(age is None or age>20 or r['stale']),transport='GITHUB_COLLECTOR_HTTPS_BATCH_5_SECONDS')
        if self.upstream.get('active') and age is not None and age>20:r['state']='RELAY_STALE'
        return r

    def close(self):
        with self.lock:
            if self.journal:self.journal.close('WEB_RELAY_PAUSED')


class RelayPublisher:
    def __init__(self,service,url,token):
        parts=urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('WEB_RELAY_HTTPS_ORIGIN_REQUIRED')
        if parts.path not in ('','/'):raise ValueError('WEB_RELAY_ORIGIN_ONLY')
        if len(token)<32:raise ValueError('WEB_RELAY_TOKEN_REQUIRED')
        self.service=service;self.url=url.rstrip('/')+'/api/relay';self.token=token;self.cursor=0
        self.stop=threading.Event();self.thread=None;self.errors=0;self.last_success=None;self.last_error=None
        context=ssl.create_default_context()
        if os.name=='nt':
            import truststore
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.opener=build_opener(HTTPSHandler(context=context),NoRedirect())

    def packet(self):
        import pyarrow.dataset as ds
        with self.service.lock:
            j=self.service.journal
            m={k:j.manifest[k] for k in ('run_id','day','symbols','created_at','normalization_version') if k in j.manifest}
            m['unit_contract']=public_contract(j.manifest.get('unit_contract',{}))
            files=list(j.manifest['files']);buffer=list(j.buffer);folder=j.folder;status=self.service.status()
        records=[];size=0
        for f in files:
            if f['last']<self.cursor:continue
            table=ds.dataset(folder/f['path'],format='parquet').to_table(columns=['record_json'],filter=ds.field('ordinal')>=self.cursor)
            for row in table.to_pylist():
                r=json.loads(row['record_json']);size+=len(row['record_json'])
                if records and (size>MAX_BODY//2 or len(records)>=MAX_RECORDS):break
                records.append(r)
            if size>MAX_BODY//2 or len(records)>=MAX_RECORDS:break
        if len(records)<MAX_RECORDS and size<=MAX_BODY//2:
            for r in buffer:
                if r['ordinal']>=self.cursor:
                    encoded=json.dumps(r).encode();size+=len(encoded)
                    if records and size>MAX_BODY//2:break
                    records.append(r)
                    if len(records)>=MAX_RECORDS:break
        return dict(manifest=m,status=status,records=records)

    def once(self):
        packet=self.packet();body=json.dumps(packet,separators=(',',':')).encode()
        if len(body)>MAX_BODY:raise ValueError('WEB_RELAY_LIMIT')
        request=Request(self.url,data=gzip.compress(body,compresslevel=1),method='POST',headers={
            'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Content-Encoding':'gzip'})
        with self.opener.open(request,timeout=65) as response:
            raw=response.read(2049)
        if len(raw)>2048:raise ValueError('WEB_RELAY_RESPONSE_LIMIT')
        result=json.loads(raw);cursor=result['next_ordinal']
        if type(cursor) is not int or not 0<=cursor<=self.service.journal.ordinal+1:raise ValueError('WEB_RELAY_RESPONSE_CURSOR')
        self.cursor=cursor;self.last_success=datetime.now(timezone.utc).isoformat();self.last_error=None

    def start(self):
        def worker():
            while not self.stop.is_set():
                try:self.once()
                except Exception as error:self.errors+=1;self.last_error=type(error).__name__
                if self.cursor<=self.service.journal.ordinal and not self.last_error:continue
                self.stop.wait(5)
        self.thread=threading.Thread(target=worker,daemon=True,name='ssi-web-relay');self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=70)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            try:self.once()
            except Exception as error:self.errors+=1;self.last_error=type(error).__name__;break
            if self.cursor>self.service.journal.ordinal:break
        return self.status()

    def status(self):
        return dict(enabled=True,next_ordinal=self.cursor,pending=max(0,self.service.journal.ordinal+1-self.cursor),
                    errors=self.errors,last_error=self.last_error,last_success_at=self.last_success)
