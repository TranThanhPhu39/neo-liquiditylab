"""Durable unscaled market fields, every receipt, connection attempts and known gaps."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
import zlib
from zoneinfo import ZoneInfo

FORMAT = 'dnse-ws-market-fields-v1'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class RealtimeStore:
    def __init__(self, path, catalog, day, partition):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS symbols(symbol TEXT PRIMARY KEY,market_id TEXT NOT NULL,exchange TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,
                state TEXT NOT NULL,mode TEXT NOT NULL,expected_connections INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
                shard INTEGER NOT NULL,symbol_count INTEGER NOT NULL,started_at TEXT NOT NULL,
                authenticated_at TEXT,subscribed_at TEXT,closed_at TEXT,error_code TEXT);
            CREATE TABLE IF NOT EXISTS events(event_key TEXT PRIMARY KEY,symbol TEXT NOT NULL REFERENCES symbols(symbol),
                source_time_utc TEXT,first_received_at TEXT NOT NULL,source_day TEXT,
                payload_zlib BLOB NOT NULL,payload_sha256 TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ws_events_symbol_time ON events(symbol,source_time_utc);
            CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,event_key TEXT NOT NULL REFERENCES events(event_key),
                connection_id TEXT NOT NULL REFERENCES connections(id),received_at TEXT NOT NULL,frame_sha256 TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ws_receipts_event ON receipts(event_key);
            CREATE TABLE IF NOT EXISTS gaps(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
                shard INTEGER NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,reason TEXT NOT NULL);
        ''')
        settings = {'format': FORMAT, 'day': day, 'partition': partition,
                    'catalog_hash': hashlib.sha256(canonical(catalog).encode()).hexdigest(),
                    'unit_status': 'UNVERIFIED_WEBSOCKET', 'numeric_encoding': 'JSON_FRACTIONS_AS_TEXT'}
        existing = self.db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()
        if existing and json.loads(existing[0]) != settings:
            self.db.close()
            raise ValueError('WS_ARCHIVE_SETTINGS_CHANGED')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('settings',?)", (canonical(settings),))
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('catalog',?)", (canonical(catalog),))
            self.db.executemany('INSERT OR IGNORE INTO symbols VALUES(:symbol,:market_id,:exchange)', catalog['rows'])

    def begin_run(self, mode, expected):
        now = utcnow()
        with self.db:
            # A restored snapshot can contain sessions that were live when copied.
            for row in self.db.execute('''SELECT c.run_id,c.shard,COALESCE(max(r.received_at),min(c.started_at)) AS anchor
                FROM connections c LEFT JOIN receipts r ON r.connection_id=c.id
                JOIN runs u ON u.id=c.run_id WHERE u.finished_at IS NULL GROUP BY c.run_id,c.shard''').fetchall():
                self.db.execute('INSERT INTO gaps(run_id,shard,started_at,finished_at,reason) VALUES(?,?,?,?,?)',
                                (row['run_id'],row['shard'],row['anchor'],now,'CHECKPOINT_RESTORE_UNCERTAIN_INTERVAL'))
            self.db.execute('UPDATE gaps SET finished_at=? WHERE finished_at IS NULL', (now,))
            self.db.execute("UPDATE runs SET state='INTERRUPTED_CHECKPOINT',finished_at=? WHERE finished_at IS NULL", (now,))
            self.db.execute("UPDATE connections SET closed_at=?,error_code='RESTORED_FROM_LIVE_CHECKPOINT' WHERE closed_at IS NULL", (now,))
            run_id = uuid.uuid4().hex
            self.db.execute('INSERT INTO runs VALUES(?,?,NULL,?,?,?)', (run_id, now, 'RUNNING', mode, expected))
        return run_id

    def connection(self, run_id, shard, count):
        connection_id = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO connections(id,run_id,shard,symbol_count,started_at) VALUES(?,?,?,?,?)',
                            (connection_id, run_id, shard, count, utcnow()))
        return connection_id

    def transition(self, connection_id, column):
        if column not in ('authenticated_at', 'subscribed_at'):
            raise ValueError('INVALID_CONNECTION_TRANSITION')
        with self.db:
            self.db.execute(f'UPDATE connections SET {column}=? WHERE id=?', (utcnow(), connection_id))

    def disconnect(self, connection_id, reason):
        with self.db:
            self.db.execute('UPDATE connections SET closed_at=?,error_code=? WHERE id=?', (utcnow(), reason, connection_id))

    def gap(self, run_id, shard, started, finished, reason):
        with self.db:
            return self.db.execute('INSERT INTO gaps(run_id,shard,started_at,finished_at,reason) VALUES(?,?,?,?,?)',
                                   (run_id, shard, started, finished, reason)).lastrowid

    def finish_gap(self, gap_id):
        with self.db:
            self.db.execute('UPDATE gaps SET finished_at=? WHERE id=?', (utcnow(), gap_id))

    def append(self, batch):
        with self.db:
            for connection_id, observation in batch:
                payload = observation['payload']
                text = canonical(payload).encode()
                digest = hashlib.sha256(text).hexdigest()
                clock = observation['source_time_utc']
                source_day = (datetime.fromisoformat(clock).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
                              if clock else None)
                self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)',
                                (digest, payload['symbol'], clock, observation['received_at_utc'], source_day,
                                 zlib.compress(text, 1), digest))
                self.db.execute('UPDATE events SET first_received_at=min(first_received_at,?) WHERE event_key=?',
                                (observation['received_at_utc'], digest))
                self.db.execute('INSERT INTO receipts(event_key,connection_id,received_at,frame_sha256) VALUES(?,?,?,?)',
                                (digest, connection_id, observation['received_at_utc'], observation['original_frame_sha256']))

    def status(self, run_id):
        events = self.db.execute('SELECT count(*) FROM events').fetchone()[0]
        receipts = self.db.execute('SELECT count(*) FROM receipts').fetchone()[0]
        rows = self.db.execute('''SELECT count(*) AS attempts,sum(authenticated_at IS NOT NULL) AS authenticated,
            sum(subscribed_at IS NOT NULL) AS subscribed FROM connections WHERE run_id=?''', (run_id,)).fetchone()
        expected = self.db.execute('SELECT expected_connections FROM runs WHERE id=?', (run_id,)).fetchone()[0]
        return {'unique_events': events, 'receipt_count': receipts, 'duplicate_receipts': receipts-events,
                'symbols_requested': self.db.execute('SELECT count(*) FROM symbols').fetchone()[0],
                'symbols_observed': self.db.execute('SELECT count(DISTINCT symbol) FROM events').fetchone()[0],
                'connection_attempts': rows['attempts'], 'authenticated_connections': rows['authenticated'] or 0,
                'subscription_acknowledgements': rows['subscribed'] or 0, 'expected_connections': expected,
                'shards_subscribed': self.db.execute('SELECT count(DISTINCT shard) FROM connections WHERE run_id=? AND subscribed_at IS NOT NULL', (run_id,)).fetchone()[0],
                'known_gaps': self.db.execute('SELECT count(*) FROM gaps').fetchone()[0],
                'unit_status': 'UNVERIFIED_WEBSOCKET', 'finance_enabled': False}

    def finish(self, run_id, state):
        with self.db:
            self.db.execute('UPDATE runs SET finished_at=?,state=? WHERE id=?', (utcnow(), state, run_id))
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def close(self):
        self.db.close()


def audit(path, verify_payloads=True):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        integrity = db.execute('PRAGMA quick_check').fetchone()[0]
        fk = len(db.execute('PRAGMA foreign_key_check').fetchall())
        checked = 0
        if verify_payloads:
            for digest, packed, saved, symbol, market in db.execute('''SELECT e.event_key,e.payload_zlib,e.payload_sha256,
                e.symbol,s.market_id FROM events e JOIN symbols s ON s.symbol=e.symbol'''):
                payload = zlib.decompress(packed)
                if hashlib.sha256(payload).hexdigest() != digest or saved != digest:
                    raise ValueError('WS_PAYLOAD_CHECKSUM_FAILED')
                value = json.loads(payload)
                if value.get('symbol') != symbol or value.get('marketId') != market or value.get('boardId') != 'G1':
                    raise ValueError('WS_PAYLOAD_SCOPE_FAILED')
                checked += 1
        orphaned = db.execute('SELECT count(*) FROM events e WHERE NOT EXISTS(SELECT 1 FROM receipts r WHERE r.event_key=e.event_key)').fetchone()[0]
        if integrity != 'ok' or fk or orphaned:
            raise ValueError('WS_DATABASE_INTEGRITY_FAILED')
        return {'state': 'VERIFIED', 'sqlite_integrity': integrity, 'foreign_key_errors': fk,
                'payloads_checked': checked, 'unique_events': db.execute('SELECT count(*) FROM events').fetchone()[0],
                'receipt_count': db.execute('SELECT count(*) FROM receipts').fetchone()[0],
                'units_verified': False, 'financial_eligible': False}
