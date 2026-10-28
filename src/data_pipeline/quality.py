from collections import defaultdict
from datetime import datetime

from src.data_pipeline.storage import quote_fingerprint


def quality_report(storage, provider=None, expected_interval=180):
    snapshots = storage.query(provider=provider)
    groups = defaultdict(list)
    for snapshot in snapshots:
        groups[(snapshot.provider, snapshot.symbol)].append(snapshot)
    unchanged, pairs, late = 0, 0, 0
    gaps = []
    for (source, symbol), rows in groups.items():
        for previous, current in zip(rows, rows[1:]):
            pairs += 1
            unchanged += quote_fingerprint(previous) == quote_fingerprint(current)
            late += current.observation_time < previous.observation_time
            # Only same local day and scheduled continuous session: no overnight/lunch false gaps.
            from src.data_pipeline.validation import scheduled_session
            a, b = previous.received_at_local, current.received_at_local
            seconds = (b - a).total_seconds()
            same_window = (a.hour < 12) == (b.hour < 12)
            if a.date() == b.date() and same_window and all(scheduled_session(t) == "SCHEDULED_CONTINUOUS" for t in (a,b)) and seconds > expected_interval * 1.5:
                gaps.append({"provider": source, "symbol": symbol, "from": a.isoformat(), "to": b.isoformat(), "seconds": seconds})
    run_sql = "SELECT status,COUNT(*) AS n FROM collection_runs" + (" WHERE provider=?" if provider else "") + " GROUP BY status"
    runs = {r["status"]: r["n"] for r in storage.connection.execute(run_sql, (provider,) if provider else ())}
    real = [s for s in snapshots if s.provider != "demo"]
    return {"provider": provider or "all (sources labeled)", "snapshots": len(snapshots),
        "symbols": len({s.symbol for s in snapshots}), "collection_days": len({s.received_at_local.date() for s in snapshots}),
        "real_observed_days": len({s.received_at_local.date() for s in real}),
        "sessions_with_verified_source_time": len({s.source_timestamp.astimezone(s.received_at_local.tzinfo).date() for s in real if s.source_timestamp and s.market_session not in ("UNKNOWN", "CLOSED")}),
        "valid_ratio": sum(s.data_quality_status == "VALID" for s in snapshots) / len(snapshots) if snapshots else None,
        "missing_level_ratio": sum(any(i.startswith(("MISSING:bid_", "MISSING:ask_")) for i in s.issues) for s in snapshots) / len(snapshots) if snapshots else None,
        "unchanged_ratio": unchanged / pairs if pairs else None, "late_observations": late,
        "failed_runs": runs.get("FAILED", 0), "runs": runs, "gap_candidates": gaps,
        "latest_received": snapshots[-1].received_at_local.isoformat() if snapshots else None,
        "note": "Ngày thu thập không đồng nghĩa phiên giao dịch đã xác minh; gap là khoảng thiếu mẫu tiềm năng, không chứng minh mất kết nối."}
