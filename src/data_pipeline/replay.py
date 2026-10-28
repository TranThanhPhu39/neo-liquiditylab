def replay(storage, *, symbol=None, provider=None, start=None, end=None):
    """Event-time order; no generated history or book interpolation."""
    return sorted(storage.query(symbol=symbol, provider=provider, start=start, end=end),
                  key=lambda s: (s.observation_time, s.collected_at_utc))


def latest_at(snapshots, at):
    if at.tzinfo is None:
        raise ValueError("Replay time requires timezone")
    latest = {}
    for snapshot in sorted(snapshots, key=lambda s: s.observation_time):
        # Both arrival time and event time must be in the past: no look-ahead.
        if snapshot.observation_time <= at and snapshot.collected_at_utc <= at:
            latest[snapshot.symbol] = snapshot
    return latest
