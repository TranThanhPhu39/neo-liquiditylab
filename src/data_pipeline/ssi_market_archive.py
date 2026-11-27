"""Bounded raw SSI stock archives, kept outside the public replay catalog."""
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from src.data_pipeline.ssi_live_store import LiveJournal, atomic

SEGMENT_TARGET_BYTES = 128 * 1024 * 1024


class MarketSegment:
    def __init__(self, root, catalog, session_id, number, *, now):
        day = now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
        folder = root / 'data/realtime/ssi-market' / day / uuid.uuid4().hex
        self.lock = threading.RLock()
        self.quote_indexes = Counter()
        self.journal = LiveJournal(root, sorted(catalog), folder=folder, batch_size=4096,
                                   flush_seconds=3, sync_each_record=False)
        self.journal.manifest.update(capture_scope='SSI_EQUITY_MARKET', exchanges=catalog,
                                     market_session_id=session_id, segment=number)
        atomic(self.journal.manifest_path, self.journal.manifest)

    def status(self):
        return dict(state=self.journal.manifest['state'], active=False,
                    capture_scope='SSI_EQUITY_MARKET', provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE')


class SSIMarketArchive:
    def __init__(self, root, catalog, *, segment_seconds=900, clock=time.monotonic):
        self.root, self.catalog = Path(root), dict(catalog)
        self.segment_seconds, self.clock = segment_seconds, clock
        self.session_id = uuid.uuid4().hex
        self.number = 0
        self.current = None
        self.sealed = deque()
        self.lock = threading.RLock()
        self.quote_counts, self.trade_counts = Counter(), Counter()
        self.first_received = self.last_received = None

    def _seal(self):
        if self.current:
            self.current.journal.close('SSI_MARKET_SEGMENT_SEALED')
            self.sealed.append(self.current)
            self.current = None

    def _rotate(self):
        if self.current and (self.clock() >= self.cut_at or self.current.journal.bytes_written >= SEGMENT_TARGET_BYTES):
            self._seal()

    def receive(self, row):
        kind, symbol = row['kind'], row['symbol']
        if kind not in ('QUOTE', 'TRADE'):
            return
        if row.get('exchange') != self.catalog.get(symbol) or symbol not in self.catalog:
            raise ValueError('SSI_MARKET_ARCHIVE_SYMBOL_SCOPE')
        with self.lock:
            self._rotate()
            if self.current is None:
                self.number += 1
                self.current = MarketSegment(self.root, self.catalog, self.session_id, self.number,
                                             now=datetime.fromisoformat(row['received_at']))
                self.cut_at = self.clock() + self.segment_seconds
            if kind == 'QUOTE':
                row = dict(row, quote_index=self.current.quote_indexes[symbol])
                self.current.quote_indexes[symbol] += 1
            self.current.journal.append(row)
            self.current.journal.tick()
            self.first_received = self.first_received or row['received_at']
            self.last_received = row['received_at']
            (self.quote_counts if kind == 'QUOTE' else self.trade_counts)[symbol] += 1

    def take_sealed(self, *, final=False):
        with self.lock:
            if final:
                self._seal()
            else:
                self._rotate()
            if not self.sealed:
                return None
            return self.sealed.popleft()

    def tick(self):
        with self.lock:
            self._rotate()
            if self.current:
                self.current.journal.tick()

    def status(self):
        with self.lock:
            boards = {}
            for board in sorted(set(self.catalog.values())):
                symbols = [symbol for symbol, exchange in self.catalog.items() if exchange == board]
                boards[board] = dict(catalog_symbols=len(symbols),
                    symbols_with_quotes=sum(self.quote_counts[s] > 0 for s in symbols),
                    symbols_with_trades=sum(self.trade_counts[s] > 0 for s in symbols),
                    quotes=sum(self.quote_counts[s] for s in symbols), trades=sum(self.trade_counts[s] for s in symbols))
            return dict(market_session_id=self.session_id, boards=boards, segments=self.number,
                        first_received_at=self.first_received, last_received_at=self.last_received,
                        provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE')
