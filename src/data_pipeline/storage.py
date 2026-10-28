from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from src.data_pipeline.normalization import normalize


def safe_raw(raw):
    """Allowlist market data only; never persist arbitrary credentials/account payloads."""
    allowed = {"symbol", "exchange", "source_timestamp", "collected_at_utc", "market_session",
               "reference_price", "last_price", "close_price", "event_id"} | {
        f"{side}_{kind}_{i}" for side in ("bid", "ask") for kind in ("price", "vol") for i in range(1, 4)}
    return {k: v for k, v in raw.items() if k in allowed}


def quote_fingerprint(snapshot):
    r = snapshot.to_record()
    fields = {k: v for k, v in r.items() if k.startswith(("bid_", "ask_")) or k in ("reference_price", "last_price")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class Storage:
    def __init__(self, path, timeout=5):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=timeout)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS collection_runs (
          id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          provider TEXT, status TEXT NOT NULL, requested_symbols TEXT,
          inserted INTEGER DEFAULT 0, error_category TEXT);
        CREATE TABLE IF NOT EXISTS orderbook_snapshots (
          id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, run_id INTEGER,
          symbol TEXT NOT NULL, provider TEXT NOT NULL, upstream_source TEXT,
          collected_at_utc TEXT NOT NULL, source_timestamp TEXT, local_date TEXT,
          market_session TEXT, quality TEXT, fingerprint TEXT, payload TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES collection_runs(id));
        CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON orderbook_snapshots(provider,symbol,collected_at_utc);
        CREATE TABLE IF NOT EXISTS orderbook_levels (
          snapshot_id INTEGER, side TEXT, level INTEGER, price TEXT, volume INTEGER,
          PRIMARY KEY(snapshot_id,side,level), FOREIGN KEY(snapshot_id) REFERENCES orderbook_snapshots(id));
        CREATE TABLE IF NOT EXISTS raw_observations (
          snapshot_id INTEGER PRIMARY KEY, payload TEXT NOT NULL,
          FOREIGN KEY(snapshot_id) REFERENCES orderbook_snapshots(id));
        CREATE TABLE IF NOT EXISTS data_quality_issues (
          snapshot_id INTEGER, issue TEXT NOT NULL,
          FOREIGN KEY(snapshot_id) REFERENCES orderbook_snapshots(id));
        CREATE TABLE IF NOT EXISTS raw_quote_batches (
          id INTEGER PRIMARY KEY, run_id INTEGER, provider TEXT NOT NULL,
          upstream_source TEXT NOT NULL, received_at_utc TEXT NOT NULL,
          provider_version TEXT NOT NULL, units_status TEXT NOT NULL,
          payload TEXT NOT NULL, UNIQUE(provider,upstream_source,received_at_utc),
          FOREIGN KEY(run_id) REFERENCES collection_runs(id));
        CREATE TABLE IF NOT EXISTS unit_contracts (
          contract_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, recorded_at_utc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshot_lineage (
          raw_batch_id INTEGER NOT NULL, symbol TEXT NOT NULL, contract_hash TEXT NOT NULL,
          snapshot_id INTEGER NOT NULL, normalized_at_utc TEXT NOT NULL,
          PRIMARY KEY(raw_batch_id,symbol),
          FOREIGN KEY(raw_batch_id) REFERENCES raw_quote_batches(id),
          FOREIGN KEY(snapshot_id) REFERENCES orderbook_snapshots(id),
          FOREIGN KEY(contract_hash) REFERENCES unit_contracts(contract_hash));
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        nested = self.connection.in_transaction
        if nested:
            self.connection.execute("SAVEPOINT liquidity_nested")
        try:
            yield
            if nested:
                self.connection.execute("RELEASE SAVEPOINT liquidity_nested")
            else:
                self.connection.commit()
        except BaseException:
            if nested:
                self.connection.execute("ROLLBACK TO SAVEPOINT liquidity_nested")
                self.connection.execute("RELEASE SAVEPOINT liquidity_nested")
            else:
                self.connection.rollback()
            raise

    def start_run(self, provider, symbols, min_interval=0):
        with self.transaction():
            self.connection.execute("BEGIN IMMEDIATE")
            if min_interval:
                last = self.connection.execute("SELECT MAX(started_at) FROM collection_runs WHERE provider=?",(provider,)).fetchone()[0]
                if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() < min_interval:
                    from src.data_providers.base import LocalRateLimit
                    raise LocalRateLimit("LOCAL_RATE_LIMIT: chưa đủ khoảng nghỉ giữa các lần thu nguồn này trong database")
            cursor = self.connection.execute("INSERT INTO collection_runs(started_at,provider,status,requested_symbols) VALUES(?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), provider, "RUNNING", json.dumps(list(symbols))))
        return cursor.lastrowid

    def collection_wait_seconds(self, provider, min_interval=180, now=None):
        last = self.connection.execute("SELECT MAX(started_at) FROM collection_runs WHERE provider=?",(provider,)).fetchone()[0]
        if not last:
            return 0.0
        elapsed = ((now or datetime.now(timezone.utc))-datetime.fromisoformat(last)).total_seconds()
        return max(0.0,min_interval-elapsed)

    def finish_run(self, run_id, status, inserted=0, error=None):
        with self.transaction():
            self.connection.execute("UPDATE collection_runs SET finished_at=?,status=?,inserted=?,error_category=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), status, inserted, error, run_id))

    def insert(self, snapshot, run_id=None):
        record = snapshot.to_record()
        event_identity = snapshot.event_id or record["collected_at_utc"]
        key = hashlib.sha256(json.dumps([snapshot.provider, snapshot.upstream_source, snapshot.symbol, event_identity]).encode()).hexdigest()
        for attempt in range(3):
            try:
                with self.transaction():
                    cursor = self.connection.execute("""INSERT OR IGNORE INTO orderbook_snapshots
                      (event_key,run_id,symbol,provider,upstream_source,collected_at_utc,source_timestamp,local_date,market_session,quality,fingerprint,payload)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (key, run_id, snapshot.symbol, snapshot.provider,
                        snapshot.upstream_source, record["collected_at_utc"], record["source_timestamp"],
                        snapshot.received_at_local.date().isoformat(), snapshot.market_session, snapshot.data_quality_status,
                        quote_fingerprint(snapshot), json.dumps(record)))
                    if cursor.rowcount == 0:
                        return False
                    snapshot_id = cursor.lastrowid
                    self.connection.execute("INSERT INTO raw_observations VALUES(?,?)", (snapshot_id, json.dumps(safe_raw(snapshot.raw), default=str)))
                    for side, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
                        self.connection.executemany("INSERT INTO orderbook_levels VALUES(?,?,?,?,?)", [
                            (snapshot_id, side, x.level, str(x.price) if x.price is not None else None, x.volume) for x in levels])
                    self.connection.executemany("INSERT INTO data_quality_issues VALUES(?,?)", [(snapshot_id, issue) for issue in snapshot.issues])
                return True
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 2:
                    raise
                time.sleep(0.1 * 2 ** attempt)

    def query(self, *, symbol=None, provider=None, start=None, end=None, quality=None, session=None):
        clauses, params = [], []
        for column, value in (("symbol", symbol), ("provider", provider), ("quality", quality), ("market_session", session)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        for value, op in ((start, ">="), (end, "<=")):
            if value is not None:
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if value.tzinfo is None:
                    raise ValueError("Bộ lọc thời gian cần timezone")
                clauses.append(f"collected_at_utc {op} ?")
                params.append(value.astimezone(timezone.utc).isoformat())
        sql = "SELECT payload FROM orderbook_snapshots" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY collected_at_utc,id"
        snapshots = []
        for row in self.connection.execute(sql, params):
            record = json.loads(row["payload"])
            snapshots.append(normalize(record, provider=record["provider"], upstream_source=record["upstream_source"],
                capture_method=record["capture_method"], provider_version=record["provider_version"]))
        return snapshots

    def integrity_check(self):
        return self.connection.execute("PRAGMA integrity_check").fetchone()[0]

    def insert_raw_batch(self, response, run_id):
        from src.data_providers.diagnostics import MARKET_FIELDS
        received = datetime.fromisoformat(response["received_at_utc"])
        if received.tzinfo is None:
            raise ValueError("Raw reception timestamp requires timezone")
        payload = {"rows":[{k:v for k,v in row.items() if k in MARKET_FIELDS} for row in response["rows"]],
                   "source_timestamp":None,"market_session":"UNKNOWN","capture_method":"rest_poll_raw",
                   "missing_columns":response.get("missing_columns",[])}
        with self.transaction():
            cursor = self.connection.execute("""INSERT OR IGNORE INTO raw_quote_batches
                (run_id,provider,upstream_source,received_at_utc,provider_version,units_status,payload)
                VALUES(?,?,?,?,?,?,?)""",(run_id,"vnstock","kbs",received.astimezone(timezone.utc).isoformat(),
                    response["version"],"UNVERIFIED",json.dumps(payload)))
        return cursor.rowcount > 0

    def raw_batches(self, provider="vnstock", *, batch_ids=None):
        clauses,params = ["provider=?"],[provider]
        if batch_ids is not None:
            if not batch_ids:
                return []
            clauses.append("id IN ("+",".join("?" for _ in batch_ids)+")")
            params.extend(batch_ids)
        return [{**dict(row),"payload":json.loads(row["payload"])} for row in self.connection.execute(
            "SELECT * FROM raw_quote_batches WHERE "+" AND ".join(clauses)+" ORDER BY received_at_utc,id",params)]

    def backup_to(self, destination):
        target = Path(destination)
        if self.path != ":memory:" and target.resolve() == Path(self.path).resolve():
            raise ValueError("Backup path must differ from source")
        if target.exists():
            raise FileExistsError("Không ghi đè backup đã có")
        target.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(target)) as backup:
            self.connection.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
        return str(target)
