"""Indexed, read-only access to DNSE backfills; never performs network requests."""
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import zlib

from src.models.orderbook import LOCAL_TZ


def day_bounds(day):
    start = datetime.combine(date.fromisoformat(day), time(), LOCAL_TZ)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def utc_text(at):
    if at.tzinfo is None:
        raise ValueError('Timezone-aware cutoff required')
    # Canonical storage uses isoformat(): omit .000000 for exact second matches.
    return at.astimezone(timezone.utc).isoformat()


class DNSEHistory:
    def __new__(cls, path, *, prefer_parquet=True):
        path = Path(path).resolve()
        catalog = path if path.name == 'catalog.json' else (
            path.parents[2] / 'compact/catalog.json' if path.parts[-4:] == ('data','processed','dnse','normalized.db') else None)
        if prefer_parquet and catalog and catalog.exists():
            from src.data_pipeline.parquet_history import ParquetHistory
            return ParquetHistory(catalog)
        return super().__new__(cls)

    def __init__(self, path, *, prefer_parquet=True):
        self.path = Path(path).resolve()

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        return db

    def records(self, symbol, day):
        start, end = day_bounds(day)
        with closing(self.connect()) as db:
            return [json.loads(zlib.decompress(r[0])) for r in db.execute(
                'SELECT payload_zlib FROM snapshots WHERE symbol=? AND source_time>=? AND source_time<? ORDER BY source_time,id',
                (symbol, utc_text(start), utc_text(end)))]

    def dates(self):
        with closing(self.connect()) as db:
            # Archive settings were validated against every event on ingestion.
            # Read this small catalog instead of scanning the million-row date index.
            return sorted({json.loads(row[0])['date'] for row in db.execute('SELECT settings FROM archives')})

    def catalog(self, day):
        """Keep empty API responses distinct from actual zero-valued snapshots."""
        date.fromisoformat(day)
        with closing(self.connect()) as db:
            archives = [row['id'] for row in db.execute('SELECT id,settings FROM archives')
                        if json.loads(row['settings'])['date'] == day]
            result = {}
            for archive in archives:
                for row in db.execute('SELECT symbol,exchange,status,raw_quote_count FROM raw_catalog WHERE archive_id=?', (archive,)):
                    entry = result.setdefault(row['symbol'], dict(row, archive_statuses=[]))
                    entry['archive_statuses'].append(row['status'])
                    entry['raw_quote_count'] = max(entry['raw_quote_count'], row['raw_quote_count'])
            return result

    def manual_checks(self, day, symbol):
        with closing(self.connect()) as db:
            return [dict(session=row['session'], evidence=json.loads(row['evidence'])) for row in db.execute(
                'SELECT session,evidence FROM manual_checks WHERE source_date=? AND symbol=? ORDER BY session', (day, symbol))]

    def window(self, symbol, day):
        start, end = day_bounds(day)
        with closing(self.connect()) as db:
            row = db.execute('''SELECT count(*) AS snapshots, min(source_time) AS first_source_time,
                max(source_time) AS last_source_time, sum(financial_eligible) AS financial_eligible
                FROM snapshots WHERE symbol=? AND source_time>=? AND source_time<?''',
                (symbol, utc_text(start), utc_text(end))).fetchone()
            return dict(row)

    def reconstruct_at(self, symbols, day, at):
        """Ex-post reconstruction by event time, NOT what was received at that time.

        Preserve actual receipt clocks in the payload. The ordinary replay.latest_at
        arrival-time contract remains unchanged.
        """
        start, end = day_bounds(day)
        cutoff = utc_text(at)
        if not start <= at.astimezone(timezone.utc) < end:
            raise ValueError('Cutoff must belong to the selected source day')
        result = {}
        with closing(self.connect()) as db:
            for symbol in dict.fromkeys(symbols):
                row = db.execute('''SELECT payload_zlib FROM snapshots
                    WHERE symbol=? AND source_time>=? AND source_time<=?
                    ORDER BY source_time DESC,id DESC LIMIT 1''', (symbol, utc_text(start), cutoff)).fetchone()
                if row:
                    result[symbol] = json.loads(zlib.decompress(row[0]))
        return result

    def sampled_records(self, symbol, day, at, limit=600):
        """Last event in each time bucket, at most limit payloads decompressed."""
        if not 2 <= limit <= 1200:
            raise ValueError('Sample limit must be between 2 and 1200')
        start, end = day_bounds(day)
        if not start <= at.astimezone(timezone.utc) < end:
            raise ValueError('Cutoff must belong to the selected source day')
        bucket_seconds = (86400 + limit - 1) // limit
        with closing(self.connect()) as db:
            rows = db.execute('''SELECT payload_zlib FROM (
                SELECT payload_zlib,source_time,id, ROW_NUMBER() OVER (
                    PARTITION BY CAST(strftime('%s',source_time) AS INTEGER) / ?
                    ORDER BY source_time DESC,id DESC) AS ordinal
                FROM snapshots WHERE symbol=? AND source_time>=? AND source_time<=?)
                WHERE ordinal=1 ORDER BY source_time,id LIMIT ?''',
                (bucket_seconds, symbol, utc_text(start), utc_text(at), limit))
            return [json.loads(zlib.decompress(row[0])) for row in rows]
