"""Lossless market-wide observation views; never grant new detector permissions."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re

from src.data_pipeline.parquet_history import confined,digest
from src.monitoring.spread import features,SpreadConfig,stamp
from src.monitoring.events import EventConfig,parameters

FORMAT='liquiditylab-market-observations-v1'
SYMBOL=re.compile(r'[A-Z][A-Z0-9]{1,11}')
TEXT_FIELDS=('symbol','exchange','board_id','event_id','source_timestamp','collected_at_utc',
    'market_session','data_quality_status','unit_verification_status','unit_contract_hash','price_unit','volume_unit')
BOOK_FIELDS=tuple(f'{side}_{kind}_{i}' for side in ('bid','ask') for i in (1,2,3) for kind in ('price','vol'))


def serial(value):
    from decimal import Decimal
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
    return value


def diagnostic(record):
    f=serial(features(record))
    f.update(eligible=False,detector_state='INHIBITED')
    f['reason']='UNVERIFIED_UNITS' if record.get('unit_verification_status') not in ('EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED') else 'MARKET_OBSERVATION_ONLY'
    return f


class MarketDocument:
    def __init__(self,store,entry):self.store=store;self.entry=entry
    def read_bytes(self):return json.dumps(self.store.replay(self.entry),ensure_ascii=False,separators=(',',':')).encode()
    def read_text(self,encoding='utf-8'):return self.read_bytes().decode(encoding)


class MarketHistory:
    def __init__(self,root):
        self.root=Path(root).resolve();self.path=self.root/'data/compact/market/catalog.json'
        self._signature=None;self._index=[];self._checked=set();self._cache=OrderedDict();self._days={};self.recover_day=None

    def index(self):
        if not self.path.exists():return []
        signature=(self.path.stat().st_mtime_ns,self.path.stat().st_size)
        if signature==self._signature:return self._index
        if signature[1]>24*1024*1024:raise ValueError('MARKET_INDEX_TOO_LARGE')
        value=json.loads(self.path.read_bytes());entries=value.get('datasets',[])
        if value.get('format')!=FORMAT or len(entries)>50000:raise ValueError('INVALID_MARKET_INDEX')
        seen=set()
        for e in entries:
            if (not SYMBOL.fullmatch(e['symbol']) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',e['day']) or
                not re.fullmatch('[a-f0-9]{64}',e['dataset_id']) or e['dataset_id'] in seen or
                e['exchange'] not in ('HOSE','HNX','UPCOM') or type(e['observations']) is not int or not 0<=e['observations']<=100000):
                raise ValueError('INVALID_MARKET_ENTRY')
            seen.add(e['dataset_id'])
            p=confined(self.root,e['market_file'])
            if not p.is_relative_to(self.root/'data/compact/market') or p.suffix!='.parquet':raise ValueError('INVALID_MARKET_FILE')
        self._index=entries;self._days=value.get('day_releases',{});self._signature=signature;self._cache.clear();self._checked.clear()
        return entries

    def entry(self,dataset_id):return next((e for e in self.index() if e['dataset_id']==dataset_id),None)

    def table(self,entry,symbols=None,cutoff=None):
        import pyarrow.dataset as ds
        p=confined(self.root,entry['market_file'])
        if not p.exists():p=self.remote_file(entry)
        identity=(str(p),entry['market_sha256'],p.stat().st_mtime_ns,p.stat().st_size)
        if identity not in self._checked:
            if p.is_symlink() or p.stat().st_size>32*1024*1024 or digest(p)!=entry['market_sha256']:raise ValueError('MARKET_CHECKSUM_MISMATCH')
            self._checked.add(identity)
        condition=ds.field('symbol').isin(symbols or [entry['symbol']])
        if cutoff is not None:condition=condition & (ds.field('source_time')<=stamp(cutoff))
        return ds.dataset(p,format='parquet').to_table(filter=condition)

    def remote_file(self,entry):
        """Restore at most two encrypted day bundles; no broker credentials."""
        tag=self._days.get(entry['day'])
        if not isinstance(tag,str) or not re.fullmatch(r'monitor-web-seed-market-\d{4}-\d{2}-\d{2}-[a-f0-9]{16}',tag):
            raise ValueError('MARKET_DAY_RECOVERY_NOT_CONFIGURED')
        cache=self.root/'data/control/market-day-cache';cache.mkdir(parents=True,exist_ok=True)
        folder=cache/hashlib.sha256(tag.encode()).hexdigest()[:24]
        p=confined(folder,entry['market_file'])
        if not p.is_file():
            from src.monitoring.web_archive import restore_seed
            from scripts.restore_ssi_checkpoint import recovery_key
            (self.recover_day or (lambda destination,release:restore_seed(destination,recovery_key(),release)))(folder,tag)
        if not p.is_file():raise ValueError('MARKET_DAY_RECOVERY_INCOMPLETE')
        folder.touch()
        # Only this dedicated derived cache is eligible, never source Parquet.
        import shutil
        for old in sorted(cache.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)[2:]:
            if old.is_dir() and not old.is_symlink() and old.resolve().parent==cache.resolve() and re.fullmatch(r'[a-f0-9]{24}',old.name):shutil.rmtree(old)
        return p

    def frames(self,entry):
        if not entry['observations']:raise ValueError('MARKET_NO_SOURCE_OBSERVATIONS')
        table=self.table(entry).sort_by([('source_time','ascending'),('source_order','ascending')])
        if table.num_rows!=entry['observations']:raise ValueError('MARKET_ROW_COUNT_MISMATCH')
        for batch in table.to_batches(max_chunksize=512):
            for r in batch.to_pylist():
                if r['symbol']!=entry['symbol'] or r['exchange']!=entry['exchange']:raise ValueError('MARKET_SCOPE_MISMATCH')
                r['provider']='dnse'
                yield dict(id=r['event_id'],time=r['source_timestamp'],received_at=r['collected_at_utc'],
                    session=r['market_session'],quality=r['data_quality_status'],units=r['unit_verification_status'],
                    bids=[[r[f'bid_price_{i}'],r[f'bid_vol_{i}']] for i in (1,2,3)],
                    asks=[[r[f'ask_price_{i}'],r[f'ask_vol_{i}']] for i in (1,2,3)],features=diagnostic(r))

    def metadata(self,entry):
        cfg=SpreadConfig();ecfg=EventConfig()
        return dict(format='liquiditylab-event-replay-v1',dataset_id=entry['dataset_id'],symbol=entry['symbol'],day=entry['day'],provider='dnse',
            exchange=entry['exchange'],board='G1',levels=3,price_unit='VND',volume_unit='shares',
            clock_policy='RETROSPECTIVE_SOURCE_ORDER_NOT_LIVE_BACKTEST',sampling='ALL_STORED_SYMBOL_OBSERVATIONS_NO_RESAMPLE',
            detector_version='market-observation-only-v1',configuration_status='DETECTORS_INHIBITED_MARKET_OBSERVATION_ONLY',
            config=cfg.payload(),detector_configs=dict(spread=cfg.payload(),events=ecfg.payload()),analysis_parameters=parameters(cfg,ecfg),
            source_archive=entry['source_archive'],quality_summary=entry['quality'],event_patches=[],
            detector_scope='OBSERVATION_ONLY_NO_NEW_UNIT_VERIFICATION',unit_rule_hash=entry['unit_rule_hash'])

    def replay(self,entry):
        key=entry['dataset_id']
        if key in self._cache:self._cache.move_to_end(key);return self._cache[key]
        result=dict(self.metadata(entry),frames=list(self.frames(entry)))
        self._cache[key]=result
        while len(self._cache)>1:self._cache.popitem(last=False)
        return result

    def at(self,entries,cutoff):
        grouped={};result={}
        for e in entries:
            if e['observations']:grouped.setdefault(e['market_file'],[]).append(e)
        for group in grouped.values():
            table=self.table(group[0],[e['symbol'] for e in group],cutoff)
            # Columnar grouping keeps only the last row per symbol at this cutoff.
            import pyarrow as pa
            table=table.sort_by([('source_time','ascending'),('source_order','ascending')])
            symbols=table.column('symbol').to_pylist();indices={s:i for i,s in enumerate(symbols)}
            for r in table.take(pa.array(list(indices.values()),type=pa.int64())).to_pylist():
                r['provider']='dnse';result[r['symbol']]=r
        return result
