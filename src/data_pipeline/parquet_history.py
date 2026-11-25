"""Read the lossless, local Parquet catalog without materializing a SQLite archive."""
from bisect import bisect_right
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib


def confined(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or Path(relative).is_absolute():
        raise ValueError('COMPACT_UNSAFE_PATH')
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def checked_folder(root, entry):
    folder = confined(root, entry['folder'])
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        if Path(item['path']).name != item['path']:
            raise ValueError('COMPACT_UNSAFE_PATH')
        p = confined(folder, item['path'])
        if p.stat().st_size != item['bytes'] or digest(p) != item['sha256']:
            raise ValueError('COMPACT_CHECKSUM_MISMATCH')
    return folder, manifest


class MetadataConnection(sqlite3.Connection):
    """Small in-memory metadata SQL compatibility for existing report tools."""


class ParquetHistory:
    def __init__(self, catalog):
        self.path = Path(catalog).resolve()
        self.root = self.path.parents[2]
        self.index = json.loads(self.path.read_text(encoding='utf-8'))
        self.days = {e['day']: e for e in self.index['quote_days']}
        self._cache = OrderedDict()
        self._metadata = {}

    def dates(self):
        return sorted(self.days)

    def folder(self, day):
        entry = self.days[day]
        if entry.get('state') == 'UNIT_REVISION_ALL_ROWS_VERIFIED':
            if not hasattr(self,'_revisions_checked'):self._revisions_checked=set()
            if day not in self._revisions_checked:
                folder,manifest=checked_folder(self.root,entry)
                if manifest.get('format')!='liquiditylab-unit-revised-parquet-day-v1' or not manifest.get('unchanged_observation_digest'):
                    raise ValueError('COMPACT_UNIT_REVISION_INVALID')
                self._revisions_checked.add(day)
            return confined(self.root,entry['folder'])
        if entry.get('state') != 'SOURCE_AND_PARQUET_ALL_ROWS_VERIFIED':
            raise ValueError('COMPACT_UNVERIFIED_DAY')
        return confined(self.root, entry['folder'])

    def metadata(self, day):
        if day not in self._metadata:
            self._metadata[day] = json.loads((self.folder(day) / 'sqlite_metadata.json').read_text(encoding='utf-8'))
        return self._metadata[day]

    def connect(self):
        # Never create/open normalized.db. Only the small provenance tables use SQL.
        db = sqlite3.connect(':memory:', factory=MetadataConnection)
        db.history = self
        db.row_factory = sqlite3.Row
        for day in self.dates():
            for table, rows in self.metadata(day).items():
                if not rows:
                    continue
                if table not in {'archives', 'raw_catalog', 'processed_pages', 'quarantine', 'normalization_rules', 'manual_checks'}:
                    raise ValueError('COMPACT_METADATA_TABLE_INVALID')
                columns = list(rows[0])
                if any(not c.replace('_', '').isalnum() for c in columns):
                    raise ValueError('COMPACT_METADATA_COLUMN_INVALID')
                db.execute(f'CREATE TABLE IF NOT EXISTS {table} (' + ','.join(columns) + ')')
                db.executemany(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in columns) + ')',
                               ([r[c] for c in columns] for r in rows))
        # Rules are repeated in every day bundle; callers expect one logical rule.
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='normalization_rules'").fetchone():
            db.execute('DELETE FROM normalization_rules WHERE rowid NOT IN (SELECT min(rowid) FROM normalization_rules GROUP BY hash)')
        db.commit()
        db.execute('PRAGMA query_only=ON')
        return db

    def catalog(self, day):
        result = {}
        metadata = self.metadata(day)
        rows = [r for a in metadata['archives'] for r in metadata['raw_catalog'] if r['archive_id'] == a['id']]
        for row in rows:
            item = result.setdefault(row['symbol'], dict(symbol=row['symbol'], exchange=row['exchange'],
                                     status=row['status'], raw_quote_count=0, archive_statuses=[]))
            item['archive_statuses'].append(row['status'])
            item['raw_quote_count'] = max(item['raw_quote_count'], row['raw_quote_count'])
        return result

    def manual_checks(self, day, symbol):
        return [dict(session=r['session'], evidence=json.loads(r['evidence']))
                for r in sorted(self.metadata(day)['manual_checks'], key=lambda r: r['session']) if r['symbol'] == symbol]

    def rows(self, symbol, day):
        import pyarrow.parquet as pq
        key = (symbol, day)
        if key not in self._cache:
            group = hashlib.sha256(symbol.encode()).digest()[0] % 4
            path = self.folder(day) / f'quotes-{group}.parquet'
            if path.exists():
                rows=[]
                for batch in pq.ParquetFile(path).iter_batches(batch_size=128,use_threads=False):
                    rows.extend(r for r in batch.to_pylist() if r['symbol']==symbol)
                rows.sort(key=lambda r:(r['source_timestamp_utc'],r['id']))
            else:
                rows = []
            self._cache[key] = rows
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return self._cache[key]

    def records(self, symbol, day):
        return [json.loads(r['record_json']) for r in self.rows(symbol, day)]

    def window(self, symbol, day):
        rows = self.rows(symbol, day)
        return dict(snapshots=len(rows), first_source_time=rows[0]['source_time'] if rows else None,
                    last_source_time=rows[-1]['source_time'] if rows else None,
                    financial_eligible=sum(r['financial_eligible'] for r in rows) if rows else None)

    def at_row(self, symbol, day, at):
        return self._latest_rows([symbol],day,at).get(symbol)

    def _latest_rows(self, symbols, day, at):
        """One row per symbol at the cutoff; never load/cache a day's payloads.

        Source timestamps can tie or be physically unordered. Keep the same
        (source time, id) ordering used by rows(), without sorting a full table.
        """
        import pyarrow.parquet as pq
        from src.data_pipeline.dnse_history import day_bounds, utc_text
        start, end = day_bounds(day)
        utc_text(at)  # Enforce aware clocks, including for an empty symbol.
        if not start <= at.astimezone(timezone.utc) < end:
            raise ValueError('Cutoff must belong to the selected source day')
        grouped={};latest={};keys={}
        for symbol in dict.fromkeys(symbols):
            group=hashlib.sha256(symbol.encode()).digest()[0]%4
            grouped.setdefault(group,[]).append(symbol)
        for group,requested in grouped.items():
            path=self.folder(day)/f'quotes-{group}.parquet'
            if not path.exists():continue
            source=pq.ParquetFile(path);selected={};offset=0
            for batch in source.iter_batches(batch_size=512,use_threads=False,
                    columns=['symbol','source_timestamp_utc','id']):
                for i,row in enumerate(batch.to_pylist()):
                    symbol=row['symbol'];key=(row['source_timestamp_utc'],row['id'])
                    if symbol in requested and row['source_timestamp_utc']<=at and (symbol not in keys or key>keys[symbol]):
                        selected[symbol]=offset+i;keys[symbol]=key
                offset+=batch.num_rows
            if not selected:continue
            # Dataset filters can decode a whole large record_json row group
            # before filtering. Parquet iter_batches bounds the actual decoder.
            positions={position:symbol for symbol,position in selected.items()};offset=0
            for group_index in range(source.metadata.num_row_groups):
                end=offset+source.metadata.row_group(group_index).num_rows
                wanted={p:s for p,s in positions.items() if offset<=p<end}
                if wanted:
                    point=offset
                    for batch in source.iter_batches(batch_size=128,row_groups=[group_index],use_threads=False):
                        for p,symbol in list(wanted.items()):
                            if point<=p<point+batch.num_rows:
                                row=batch.slice(p-point,1).to_pylist()[0]
                                if (row['symbol']!=symbol or (row['source_timestamp_utc'],row['id'])!=keys[symbol]):
                                    raise ValueError('COMPACT_CUTOFF_ROW_CHANGED')
                                latest[symbol]=row;del wanted[p]
                        point+=batch.num_rows
                        if not wanted:break
                offset=end
        return latest

    def reconstruct_at(self, symbols, day, at):
        if not symbols:return {}
        rows=self._latest_rows(symbols,day,at)
        return {s:json.loads(rows[s]['record_json']) for s in dict.fromkeys(symbols) if s in rows}

    def sampled_records(self, symbol, day, at, limit=600):
        if not 2 <= limit <= 1200:
            raise ValueError('Sample limit must be between 2 and 1200')
        self.at_row(symbol, day, at)
        buckets = {}
        seconds = (86400 + limit - 1) // limit
        for row in self.rows(symbol, day):
            if row['source_timestamp_utc'] <= at:
                buckets[int(row['source_timestamp_utc'].timestamp()) // seconds] = row
        return [json.loads(r['record_json']) for r in list(buckets.values())[:limit]]

    def grid_rows(self, symbol, day, points):
        rows = self.rows(symbol, day)
        times = [r['source_timestamp_utc'] for r in rows]
        result = []
        for _, at in points:
            i = bisect_right(times, at) - 1
            if i < 0:
                result.append(None)
            else:
                row = dict(rows[i])
                row['payload_zlib'] = zlib.compress(row.pop('record_json'), 1)
                result.append(row)
        return result

    def statistics(self, day):
        import pyarrow as pa
        import pyarrow.dataset as ds
        tables = [ds.dataset(p, format='parquet').to_table(columns=['symbol', 'source_time'])
                  for p in self.folder(day).glob('quotes-*.parquet')]
        if not tables:
            return []
        groups = pa.concat_tables(tables).group_by('symbol').aggregate([('source_time', 'count'), ('source_time', 'min'), ('source_time', 'max')])
        return [dict(symbol=r['symbol'], snapshots=r['source_time_count'], first_source_time=r['source_time_min'],
                     last_source_time=r['source_time_max']) for r in groups.to_pylist()]

    def origin(self, event_id, symbol, day):
        import pyarrow.dataset as ds
        rows = [r for r in self.rows(symbol, day) if r['event_key'] == event_id]
        if not rows:
            return None
        mapped = ds.dataset(self.folder(day)/'lineage.parquet', format='parquet').to_table(
            filter=ds.field('snapshot_id') == rows[0]['id']).to_pylist()
        archives = {r['id']: r['path'] for r in self.metadata(day)['archives']}
        origins = [dict(path=archives[r['archive_id']], page_id=r['page_id'], ordinal=r['ordinal']) for r in mapped]
        return min(origins, key=lambda r: (r['path'], r['page_id'], r['ordinal'])) if origins else None

    def trades(self, symbol, day):
        import pyarrow.parquet as pq
        entry = next((e for e in self.index.get('trade_packages', []) if e['symbol'] == symbol and e['day'] == day), None)
        if entry is None:
            return None
        folder = confined(self.root, entry['folder'])
        result = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        result['trades'] = [json.loads(r['record_json']) for r in pq.read_table(folder/'trades.parquet', columns=['record_json']).to_pylist()]
        if result['symbol'] != symbol or result['day'] != day or result['trade_dataset_id'] != entry['trade_dataset_id']:
            raise ValueError('COMPACT_TRADE_IDENTITY_MISMATCH')
        return result
