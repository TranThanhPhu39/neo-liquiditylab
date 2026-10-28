"""Descriptive report for actual captured raw observations; no volume assumptions."""
from datetime import datetime, timezone
from decimal import Decimal
import html
import json
from pathlib import Path
from statistics import median

import pandas as pd
import plotly.express as px

from src.data_pipeline.raw_collection import raw_quality_report
from src.finance.data_verification import verify_raw_prices
from src.models.orderbook import LOCAL_TZ


def build_daily_report(storage, output_stem, collector_status=None):
    stem = Path(output_stem)
    stem.parent.mkdir(parents=True,exist_ok=True)
    quality = raw_quality_report(storage)
    price_checks = verify_raw_prices(storage)
    per_symbol = []
    for symbol in quality["symbols"]:
        observations = [c for c in price_checks["checks"] if c["symbol"]==symbol]
        spreads = [Decimal(c["relative_spread_bps"]) for c in observations if c["status"]=="PRICE_CHECK_PASSED"]
        per_symbol.append({"symbol":symbol,"observations":len(observations),"valid_prices":len(spreads),
            "spread_bps_min":str(min(spreads)) if spreads else None,
            "spread_bps_median":str(median(spreads)) if spreads else None,
            "spread_bps_max":str(max(spreads)) if spreads else None})
    generated = datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat()
    state = (collector_status or {}).get("collector_state","UNKNOWN")
    report = {"generated_at_local":generated,"collector_state":state,"quality":quality,
              "per_symbol":per_symbol,"scope":"ACTUAL_RAW_PRICE_DESCRIPTIVE_ONLY",
              "execution_stress_portfolio_status":"BLOCKED_UNITS_UNVERIFIED"}
    lines = ["# Báo cáo dữ liệu thực thu · Vnstock/KBS", "",
        f"Cập nhật: {generated}. Trạng thái collector: **{state}**.", "",
        f"Đã lưu **{quality['batches']} batch / {quality['symbol_observations']} quan sát theo mã**. "
        f"Khoảng nhận: {quality['first_received_local']} → {quality['latest_received_local']}.", "",
        "**RAW ONLY · Volume chưa xác minh · Timestamp thị trường chưa có.** Không tính VWAP, coverage, proceeds, stress hoặc danh mục từ raw.", "",
        "| Mã | Quan sát | Giá hợp lệ | Spread min (bps) | Median (bps) | Max (bps) |",
        "|---|---:|---:|---:|---:|---:|"]
    def display(value):
        return "—" if value is None else f"{Decimal(value):.2f}"
    for row in per_symbol:
        lines.append(f"| {row['symbol']} | {row['observations']} | {row['valid_prices']} | "
                     f"{display(row['spread_bps_min'])} | {display(row['spread_bps_median'])} | {display(row['spread_bps_max'])} |")
    lines += ["",f"SQLite integrity: **{quality['integrity_check']}**. "
              f"Batch thiếu cột: {quality['batches_missing_columns']}. "
              f"Run request thất bại đã ghi: {quality['failed_runs']}.", "",
              "Các điểm thời gian là thời điểm nhận REST. Mẫu phụ thuộc thời gian và chỉ mô tả phần cửa sổ đã thu; không đại diện cả ngày hoặc xác suất khủng hoảng.",
              "", "Khoảng gián đoạn (>1,5 × nhịp lấy mẫu):"]
    if quality["gap_candidates"]:
        lines += [f"- {datetime.fromisoformat(g['from']).astimezone(LOCAL_TZ).isoformat()} → "
                  f"{datetime.fromisoformat(g['to']).astimezone(LOCAL_TZ).isoformat()}: {g['seconds']:.2f} giây."
                  for g in quality["gap_candidates"]]
    else:
        lines.append("- Chưa phát hiện trong dữ liệu đang có.")
    lines += ["", "Sự cố lập nhịp đã biết và cách xử lý được lưu riêng trong collection_incidents_2026-10-28.json. "
              "Run bị chặn trước reservation không nằm trong số run request thất bại.", "",
              "Bằng chứng đơn vị còn thiếu; số phiên có timestamp nguồn xác minh là 0. "
              "NEO chưa tích hợp. Không xem báo cáo này là nghiệm thu toàn bộ đồ án."]
    stem.with_suffix(".md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    passed = [c for c in price_checks["checks"] if c["status"]=="PRICE_CHECK_PASSED"]
    charts = []
    if passed:
        frame = pd.DataFrame({"Thời điểm nhận (giờ Việt Nam)":[datetime.fromisoformat(c["received_at_utc"]).astimezone(LOCAL_TZ) for c in passed],
                              "Mã":[c["symbol"] for c in passed],"Relative spread (bps)":[float(c["relative_spread_bps"]) for c in passed]})
        chart = px.line(frame,x="Thời điểm nhận (giờ Việt Nam)",y="Relative spread (bps)",color="Mã",markers=True,
                        title="Relative spread của mẫu đã nhận · không dùng volume")
        charts.append(chart.to_html(full_html=False,include_plotlyjs=True))
        counts = pd.DataFrame({"Mã":[r["symbol"] for r in per_symbol],"Số quan sát":[r["observations"] for r in per_symbol]})
        charts.append(px.bar(counts,x="Mã",y="Số quan sát",title="Số quan sát thực thu theo mã").to_html(full_html=False,include_plotlyjs=False))
    rows_html = "".join("<tr>"+"".join(f"<td>{html.escape(str(value))}</td>" for value in (
        r["symbol"],r["observations"],r["valid_prices"],display(r["spread_bps_min"]),
        display(r["spread_bps_median"]),display(r["spread_bps_max"])))+"</tr>" for r in per_symbol)
    gap_text = "".join(f"<li>{html.escape(s[2:])}</li>" for s in lines if s.startswith("- "))
    content = ("<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Báo cáo dữ liệu thực thu</title><style>body{max-width:1100px;margin:32px auto;font:16px system-ui;line-height:1.6;"
        "padding:0 20px;color:#183047;background:#f7f9fc}h1{line-height:1.2}.notice{padding:16px;background:#fff1cd;border-radius:8px}"
        "table{border-collapse:collapse;width:100%;background:white}td,th{padding:10px;border-bottom:1px solid #dbe2ea;text-align:right}"
        "td:first-child,th:first-child{text-align:left}.table{overflow-x:auto}.cards{display:flex;gap:24px;flex-wrap:wrap;margin:20px 0}"
        ".cards div{background:white;padding:16px;border-radius:8px}.cards strong{display:block;font-size:28px}</style>"
        f"<h1>Báo cáo dữ liệu thực thu</h1><p>Cập nhật {html.escape(generated)} · Collector: <b>{html.escape(state)}</b></p>"
        "<p class='notice'><b>RAW ONLY</b> · Volume chưa xác minh, chưa có timestamp thị trường. "
        "Chưa tính VWAP, coverage, proceeds, stress hoặc danh mục từ dữ liệu này.</p>"
        f"<div class='cards'><div>Batch<strong>{quality['batches']}</strong></div>"
        f"<div>Quan sát theo mã<strong>{quality['symbol_observations']}</strong></div>"
        f"<div>Integrity<strong>{html.escape(quality['integrity_check'])}</strong></div></div>"
        f"<p>Khoảng nhận: {html.escape(str(quality['first_received_local']))} → {html.escape(str(quality['latest_received_local']))}</p>"
        "<h2>Relative spread của mẫu · bps</h2><div class='table'><table><thead><tr><th>Mã</th><th>Quan sát</th>"
        "<th>Giá hợp lệ</th><th>Min</th><th>Median</th><th>Max</th></tr></thead><tbody>"+rows_html+"</tbody></table></div>"
        +"".join(charts)+"<h2>Khoảng gián đoạn</h2><ul>"+gap_text+"</ul>"
        f"<p>Batch thiếu cột: {quality['batches_missing_columns']}. Run request thất bại đã ghi: {quality['failed_runs']}.</p>"
        "<p>Mốc thời gian là thời điểm nhận REST, giờ Việt Nam. Mẫu chỉ mô tả cửa sổ đã thu, không đại diện cả ngày hoặc xác suất khủng hoảng. "
        "Sự cố chốt rate gate trước reservation được ghi riêng trong nhật ký collector.</p>"
        "<p>Chưa có bằng chứng đơn vị volume, chưa đủ nhiều phiên và chưa tích hợp NEO.</p></html>")
    stem.with_suffix(".html").write_text(content,encoding="utf-8")
    return {"markdown":str(stem.with_suffix('.md')),"html":str(stem.with_suffix('.html')),
            "json":str(stem.with_suffix('.json')),"observations":quality["symbol_observations"],"collector_state":state}
