"""Bounded connection diagnostics; reconnection does not prove tick completeness."""
from collections import deque
from datetime import datetime, timezone


class ConnectionHealth:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.pending = None
        self.recent = deque(maxlen=20)
        self.planned = self.unplanned = 0
        self.seconds = 0.0
        self.first_quotes = {}

    def state(self, state, reason, connection):
        now = self.clock()
        if state in ('RECONNECTING', 'DISCONNECTED', 'FAILED') and self.pending is None:
            planned = state == 'RECONNECTING' and reason == 'TOKEN_REFRESH'
            self.planned += int(planned)
            self.unplanned += int(not planned)
            self.pending = dict(start=now.isoformat(), reason=reason or state,
                                connection=connection, planned_token_refresh=planned)
        elif state == 'CONNECTED_WAITING_DATA' and self.pending is not None:
            gap = dict(self.pending, connected_again_at=now.isoformat())
            gap['disconnected_seconds'] = max(0.0, (now - datetime.fromisoformat(gap['start'])).total_seconds())
            self.seconds += gap['disconnected_seconds']
            self.recent.append(gap)
            self.pending = None

    def quote(self, symbol, received):
        self.first_quotes.setdefault(symbol, received)

    def status(self):
        return dict(planned_token_refreshes=self.planned, unexpected_interruptions=self.unplanned,
                    completed_disconnect_seconds=round(self.seconds, 6),
                    open_interruption=self.pending, recent_interruptions=list(self.recent),
                    first_quote_received_at=dict(self.first_quotes),
                    realtime_backfill_available=False,
                    provider_completeness='UNKNOWN_NO_PROVIDER_SEQUENCE')
