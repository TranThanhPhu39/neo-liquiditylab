"""Local SSI capture service, bounded live windows and archived Parquet replay."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time

from src.data_providers.ssi_readonly import SSIReadOnly
from src.data_providers.ssi_stream import collect, SYMBOLS
from src.data_pipeline.ssi_live_store import LiveJournal,run_folder,read_records,atomic
from src.data_pipeline.ssi_live_normalization import load_contract,normalize_quote,normalize_trade,select_session_flag,serial
from src.monitoring.events import OfflineEngine


class SSILive:
    def __init__(self,root,*,client_factory=SSIReadOnly,collector=collect):
        self.root=Path(root).resolve();self.client_factory=client_factory;self.collector=collector
        self.lock=threading.RLock();self.thread=None;self.stop_event=threading.Event();self.journal=None
        self.state='IDLE';self.reason=None;self.connection=0;self.last_received=None;self.last_quote=None
        self.first_connected_at=None
        self.frames={s:deque(maxlen=15000) for s in SYMBOLS};self.trades={s:deque(maxlen=30000) for s in SYMBOLS}
        self.patches={s:[] for s in SYMBOLS};self.counts={s:0 for s in SYMBOLS};self.flags={}
        self.engines={};self.event_times={};self.quote_total=0;self.trade_total=0;self.rejected=0;self.gaps=0
        self.trade_counts={s:0 for s in SYMBOLS}
        self.stale_symbols=set()
        self.contract=load_contract(self.root)

    def start(self,symbols,seconds=21600):
        if not isinstance(symbols,list) or not symbols or len(set(symbols))!=len(symbols) or any(s not in SYMBOLS for s in symbols):
            raise ValueError('SSI_LIVE_INVALID_SYMBOLS')
        if type(seconds) is not int or not 10<=seconds<=21600:raise ValueError('SSI_LIVE_INVALID_DURATION')
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('SSI_ALREADY_RUNNING')
            self.contract=load_contract(self.root)
            self.stop_event=threading.Event();self.journal=LiveJournal(self.root,symbols)
            self.frames={s:deque(maxlen=15000) for s in SYMBOLS};self.trades={s:deque(maxlen=30000) for s in SYMBOLS}
            self.patches={s:[] for s in SYMBOLS};self.counts={s:0 for s in SYMBOLS}
            self.event_times={}
            self.engines={s:OfflineEngine('ssi|'+self.journal.run_id+'|'+s+'|'+self.contract['hash']) for s in symbols}
            self.flags={};self.quote_total=self.trade_total=self.rejected=self.gaps=0
            self.trade_counts={s:0 for s in SYMBOLS}
            self.stale_symbols=set()
            self.last_received=self.last_quote=None;self.reason=None;self.state='STARTING'
            self.first_connected_at=None
            self.journal.manifest.update(unit_contract=self.contract,normalization_version='ssi-live-observation-v2',detector_parameters={s:e.config_hash for s,e in self.engines.items()})
            atomic(self.journal.manifest_path,self.journal.manifest)
            self.thread=threading.Thread(target=self._run,args=(symbols,seconds),daemon=True,name='ssi-data-collector')
            self.thread.start()
        return self.status()

    def _run(self,symbols,seconds):
        try:
            from src.data_pipeline.job_lock import exclusive_job
            with exclusive_job(self.root/'data/control/ssi_live.lock'):
                # Recover only after taking the collector lock, never touch an active writer.
                for p in (self.root/'data/realtime/ssi').glob('????-??-??/*/manifest.json'):
                    m=json.loads(p.read_bytes())
                    if m.get('state')=='RECORDING' and m['run_id']!=self.journal.run_id:
                        recovered=LiveJournal(self.root,m['symbols'],folder=p.parent)
                        recovered.close('RECOVERED_WITH_GAP')
                asyncio.run(self.collector(self.client_factory(),symbols,seconds,self.stop_event,self.receive,self.on_state))
        except Exception as error:
            # Never copy provider exception text, HTTP bodies or credentials into status.
            self.on_state('FAILED',dict(reason='SSI_'+type(error).__name__.upper()))
        finally:
            with self.lock:
                if self.state not in ('FAILED','STOPPED'):self.state='STOPPED'
                if self.journal:self.journal.close(self.state)

    def _new_patches(self,symbol,before,received):
        patches=[]
        for patch in self.engines[symbol].patches[before:]:
            event=dict(patch['event'])
            self.event_times.setdefault(event['id'],received)
            event['detected_received_at']=self.event_times[event['id']]
            patches.append(dict(patch,event=event,received_at=received))
        self.patches[symbol].extend(serial(patches))
        return serial(patches)

    def on_state(self,state,info):
        with self.lock:
            if state=='TICK':
                self._check_stale(datetime.now(timezone.utc));self.journal.tick();return
            if state=='SUBSCRIPTION_ACK':return
            if state=='CONNECTING':self.connection=info['connection'];self.flags={}
            self.state=state;self.reason=info.get('reason')
            now=datetime.now(timezone.utc).isoformat();patches={}
            if state=='CONNECTED_WAITING_DATA' and self.first_connected_at is None:self.first_connected_at=now
            if state in ('DISCONNECTED','RECONNECTING','FAILED','STOPPED'):
                self.flags={}
                if state!='STOPPED':self.gaps+=1
                for symbol,engine in self.engines.items():
                    before=len(engine.patches);engine.interrupt(self.counts[symbol],state)
                    patches[symbol]=self._new_patches(symbol,before,now)
            if self.journal:
                self.journal.append(dict(kind='SYSTEM',symbol='',received_at=now,state=state,reason=self.reason,
                                         connection=self.connection,patches=patches))
                self.journal.tick()

    def receive(self,observation):
        with self.lock:
            self.last_received=observation['received_at'];kind=observation['kind'];symbol=observation['symbol']
            identity=hashlib.sha256((self.journal.run_id+':'+str(self.journal.ordinal+1)).encode()).hexdigest()
            record=dict(observation)
            try:
                if kind=='MARKET':
                    p=observation['payload']
                    if p.get('f') and p.get('b')=='HOSE':
                        previous=self.flags.get('HOSE',{})
                        selected,decision=select_session_flag(previous,observation)
                        record['flag_resolution']=decision
                        if selected:self.flags['HOSE']=selected
                elif kind=='QUOTE':
                    r,levels=normalize_quote(observation,self.contract,identity,self.flags.get('HOSE'))
                    index=self.counts[symbol];engine=self.engines[symbol];before=len(engine.patches)
                    f=serial(engine.push(r,index));patches=self._new_patches(symbol,before,observation['received_at'])
                    frame=dict(id=identity,index=index,time=observation['received_at'],received_at=observation['received_at'],
                        source_time=r['source_timestamp'],session=r['market_session'],quality=r['data_quality_status'],
                        market_flag=r['market_flag'],market_flag_source=r['market_flag_source'],
                        market_flag_conflict=r['market_flag_conflict'],
                        units=r['unit_verification_status'],clock_delta_ms=r['source_receive_clock_delta_ms'],
                        issues=r['issues'],bids=levels['bids'],asks=levels['asks'],features=f,connection=observation['connection'])
                    record.update(quote_index=index,frame=frame,canonical=r,patches=patches)
                    self.frames[symbol].append(frame);self.counts[symbol]+=1;self.quote_total+=1
                    self.stale_symbols.discard(symbol)
                    self.last_quote=observation['received_at'];self.state='RECEIVING'
                elif kind=='TRADE':
                    trade=normalize_trade(observation,self.contract,identity)
                    trade['capture_ordinal']=self.journal.ordinal+1
                    record['trade']=trade;self.trades[symbol].append(trade);self.trade_total+=1
                    self.trade_counts[symbol]+=1
            except (ValueError,KeyError,TypeError):
                self.rejected+=1;record.update(kind='QUARANTINE',reason='SSI_DATA_VALIDATION_FAILED')
            self.journal.append(serial(record));self.journal.tick()
            self._check_stale(datetime.fromisoformat(observation['received_at']))
            if self.journal.ordinal>=2_000_000:
                self.reason='RECORD_BUDGET';self.stop_event.set()

    def _check_stale(self,now):
        for symbol,engine in self.engines.items():
            frames=self.frames[symbol]
            if not frames or symbol in self.stale_symbols or (now-datetime.fromisoformat(frames[-1]['time'])).total_seconds()<=30:continue
            self.stale_symbols.add(symbol);before=len(engine.patches)
            engine.interrupt(self.counts[symbol],'NO_RECENT_QUOTES')
            patches=self._new_patches(symbol,before,now.isoformat())
            self.journal.append(dict(kind='SYSTEM',symbol='',received_at=now.isoformat(),state='OBSERVATION_GAP',reason='NO_RECENT_QUOTES',patches={symbol:patches}))

    def stop(self):
        self.stop_event.set()
        return self.status()

    def status(self):
        with self.lock:
            now=datetime.now(timezone.utc)
            age=(now-datetime.fromisoformat(self.last_quote)).total_seconds() if self.last_quote else None
            verified_symbols=self.contract['symbols'] if self.contract['status']=='EMPIRICALLY_VERIFIED' else []
            symbols=self.journal.manifest['symbols'] if self.journal else list(SYMBOLS)
            all_verified=all(s in verified_symbols for s in symbols)
            return dict(state=self.state,active=bool(self.thread and self.thread.is_alive()),run_id=self.journal.run_id if self.journal else None,
                reason=self.reason,connection=self.connection,quote_age_seconds=round(age,2) if age is not None else None,
                first_connected_at=self.first_connected_at,
                stale=age is not None and age>15,last_received=self.last_received,quotes=self.quote_total,trades=self.trade_total,
                rejected=self.rejected,gaps=self.gaps,stale_symbols=sorted(self.stale_symbols),unit_status='EMPIRICALLY_VERIFIED' if all_verified else 'PARTIALLY_VERIFIED' if verified_symbols else 'UNVERIFIED',
                verified_quote_symbols=verified_symbols,trade_unit_status=self.contract.get('trade_status','UNVERIFIED'),
                price_unit='VND' if all_verified else 'PER_SYMBOL',volume_unit='shares' if all_verified else 'PER_SYMBOL',
                quote_counts=self.counts,trade_counts=self.trade_counts,source_session_flags=self.flags,provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE',
                quote_ages={s:round((now-datetime.fromisoformat(v[-1]['time'])).total_seconds(),2) if v else None for s,v in self.frames.items()},
                persisted_records=self.journal.manifest['last_ordinal']+1 if self.journal else 0,
                pending_records=len(self.journal.buffer) if self.journal else 0)

    def runs(self):
        result=[]
        for p in (self.root/'data/realtime/ssi').glob('????-??-??/*/manifest.json'):
            m=json.loads(p.read_bytes())
            result.append({k:m.get(k) for k in ('run_id','day','symbols','created_at','closed_at','state','quote_counts','trade_counts','unit_status')})
        return sorted(result,key=lambda r:r['created_at'],reverse=True)

    def view(self,run_id,symbol,after=-1,start=None,after_trade=-1):
        if symbol not in SYMBOLS or type(after) is not int or after< -1 or after_trade< -1 or (start is not None and start<0):raise ValueError('INVALID_SSI_VIEW')
        with self.lock:
            current=self.journal and run_id==self.journal.run_id
            if current and (start is None or self.frames[symbol] and start>=self.frames[symbol][0]['index']):
                buffer=list(self.frames[symbol]);reset=after<0 or (buffer and after<buffer[0]['index']-1)
                frames=([f for f in buffer if f['index']>=start][:5000] if start is not None else
                        buffer[-2000:] if reset else [f for f in buffer if f['index']>after][:2000])
                begin=(frames[0]['time'] if frames else buffer[0]['time'] if buffer else None)
                trades=[t for t in self.trades[symbol] if begin and t['time']>=begin and (reset or t['capture_ordinal']>after_trade)]
                # No trade IDs exist in the current SSI contract: preserve all captures.
                patches=self.patches[symbol]
                m=self.journal.manifest
            else:
                folder=run_folder(self.root,run_id);m=json.loads((folder/'manifest.json').read_bytes())
                total=m['quote_counts'].get(symbol,0);first=start if start is not None else max(0,total-2000)
                records=read_records(folder,symbol,'QUOTE',start=first,limit=5000)
                frames=[r['frame'] for r in records];reset=True
                trades=[r['trade'] for r in read_records(folder,symbol,'TRADE',limit=10000,
                    begin=frames[0]['time'] if frames else None,end=frames[-1]['time'] if frames else None)] if frames else []
                patches=self._archive_patches(folder,symbol)
            c=m.get('unit_contract',{})
            verified=c.get('status')=='EMPIRICALLY_VERIFIED' and symbol in c.get('symbols',[])
            return dict(run_id=run_id,symbol=symbol,day=m['day'],reset=reset,frames=frames,trades=trades,
                total=self.counts[symbol] if current else m['quote_counts'].get(symbol,0),
                trade_total=self.trade_counts[symbol] if current else m['trade_counts'].get(symbol,0),
                first_index=frames[0]['index'] if frames else None,last_index=frames[-1]['index'] if frames else after,
                dataset_id=self.dataset_id(run_id,symbol),price_unit='VND' if verified else 'SSI_SOURCE_PRICE',
                volume_unit='shares' if verified else 'SSI_SOURCE_QUANTITY',unit_status='EMPIRICALLY_VERIFIED' if verified else 'UNVERIFIED',
                events=self._events(patches,frames[-1]['time'] if frames else None),
                gaps=self.gaps if current else None,clock_policy=m['clock_policy'])

    @staticmethod
    def _events(patches,at):
        result={}
        for p in patches:
            if at and datetime.fromisoformat(p['received_at'])<=datetime.fromisoformat(at):
                result[p['event']['id']]=dict(p['event'],observed_at=p['received_at'])
        return list(result.values())

    @staticmethod
    def dataset_id(run_id,symbol):return hashlib.sha256(('ssi|'+run_id+'|'+symbol).encode()).hexdigest()

    def _archive_patches(self,folder,symbol):
        import pyarrow.dataset as ds
        result=[];m=json.loads((folder/'manifest.json').read_bytes())
        for part in m['files']:
            if __import__('re').fullmatch(r'part-\d{8}\.parquet',part['path']) is None:raise ValueError('INVALID_SSI_PARTITION')
            path=folder/part['path']
            if path.is_symlink():raise ValueError('INVALID_SSI_PARTITION')
            condition=((ds.field('symbol')==symbol)&(ds.field('kind')=='QUOTE'))|(ds.field('kind')=='SYSTEM')
            for row in ds.dataset(path,format='parquet').to_table(columns=['record_json'],filter=condition).to_pylist():
                r=json.loads(row['record_json'])
                result.extend(r.get('patches',[]) if r['kind']=='QUOTE' else r.get('patches',{}).get(symbol,[]))
        return result

    def events(self,run_id,symbol,at):
        if symbol not in SYMBOLS:raise ValueError('INVALID_SSI_SYMBOL')
        # Require an aware clock; compare clocks as datetimes, not arbitrary text.
        point=datetime.fromisoformat(at)
        if point.tzinfo is None:raise ValueError('SSI_AWARE_CUTOFF_REQUIRED')
        with self.lock:
            patches=self.patches[symbol] if self.journal and self.journal.run_id==run_id else self._archive_patches(run_folder(self.root,run_id),symbol)
            return self._events(patches,point.astimezone(timezone.utc).isoformat())

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)
