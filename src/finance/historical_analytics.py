import pandas as pd

from src.finance.execution_simulator import simulate_execution
from src.finance.liquidity_metrics import liquidity_metrics


def historical_frame(snapshots, quantity=3000):
    rows = []
    for s in snapshots:
        row = {"time": s.observation_time.astimezone(s.received_at_local.tzinfo),
               "received_at": s.received_at_local, "symbol": s.symbol, "provider": s.provider,
               "quality": s.data_quality_status, "source_time_known": s.source_timestamp is not None,
               "bid_depth": None, "ask_depth": None, "spread": None, "relative_spread": None, "obi": None}
        try:
            metrics, result = liquidity_metrics(s), simulate_execution(s, quantity)
            row.update({k: float(v) if v is not None else None for k, v in metrics.items() if k != "partial"})
            row.update(coverage=float(result.coverage_ratio), cost=float(result.book_walking_cost) if result.book_walking_cost is not None else None)
        except ValueError:
            row.update(coverage=None, cost=None)
        rows.append(row)
    return pd.DataFrame(rows)


def historical_summary(frame):
    if frame.empty:
        return {"snapshots": 0, "note": "NO DATA"}
    values = frame["coverage"].dropna()
    return {"snapshots": len(frame), "collection_days": frame["received_at"].map(lambda x: x.date()).nunique(),
            "providers": sorted(frame["provider"].unique().tolist()), "start": str(frame["time"].min()),
            "end": str(frame["time"].max()), "invalid_ratio": float(frame["coverage"].isna().mean()),
            "source_timestamp_missing_ratio": float((~frame["source_time_known"]).mean()),
            "coverage_min": float(values.min()) if len(values) else None,
            "coverage_median": float(values.median()) if len(values) else None,
            "coverage_p05": float(values.quantile(.05)) if len(values) >= 20 else None,
            "note": "Thống kê mô tả mẫu đã lưu; snapshot lặp không độc lập. Không phải xác suất khủng hoảng."}
