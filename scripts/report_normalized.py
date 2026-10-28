"""Export an offline historical report from normalized snapshots; no market calls."""
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

import pandas as pd
import plotly.express as px

from config.settings import Settings
from src.data_pipeline.replay import latest_at
from src.data_pipeline.storage import Storage
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.stress_testing import compare_scenario
from src.models.orderbook import LOCAL_TZ
from src.models.scenario import Scenario


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",default=Settings().database_path)
    parser.add_argument("--output-stem",default="report/analysis_2026-10-28")
    args = parser.parse_args()
    with Storage(args.db) as db:
        books = [s for s in db.query(provider="vnstock") if s.capture_method=="raw_migration"]
    if not books:
        raise ValueError("No normalized Vnstock data")
    anchor = max(s.collected_at_utc for s in books)
    days = len({s.received_at_local.date() for s in books})
    rows, curve, bars = [], [], []
    quantity = 500000
    for book in books:
        m = liquidity_metrics(book)
        for side in ("bid","ask"):
            curve.append({"Thời điểm nhận":book.received_at_local,"Mã":book.symbol,
                          "Bên":side,"Độ sâu (CP)":m[side+"_depth"]})
    for symbol,book in sorted(latest_at(books,anchor).items()):
        m = liquidity_metrics(book)
        comparison = compare_scenario(book,quantity,Scenario("0.5"))
        base,stress = comparison["baseline"], comparison["stressed"]
        rows.append({"Mã":symbol,"Bid depth (CP)":m["bid_depth"],"Ask depth (CP)":m["ask_depth"],
            "Spread (VND)":str(m["spread"]),"Coverage baseline":f"{base.coverage_ratio:.2%}",
            "Coverage giảm depth 50%":f"{stress.coverage_ratio:.2%}",
            "Uncovered baseline (CP)":base.uncovered_quantity,"Uncovered stress (CP)":stress.uncovered_quantity,
            "VWAP baseline (VND)":str(base.vwap),"Gross baseline phần bao phủ (VND)":str(base.gross_value)})
        for name,result in (("Baseline",base),("Giảm depth 50%",stress)):
            bars.append({"Mã":symbol,"Kịch bản":name,"Coverage":float(result.coverage_ratio)})
    generated = datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat()
    note = ("Dữ liệu Vnstock/KBS thực thu đã chuẩn hóa · phân tích lịch sử theo thời điểm nhận REST. "
        "Lệnh bán giả định 500.000 CP/mã, stress giảm 50% từng mức Bid, giữ giá, phí/thuế 0 cho bảng này. "
        "VWAP/gross chỉ tính phần được bao phủ; phần còn lại không có giá thực hiện. "
        "Không có timestamp nguồn, không xác nhận đồng bộ giá. Mẫu chỉ bao phủ các thời điểm thực thu, "
        "không chứng nhận phiên đầy đủ; NEO chưa tích hợp và không có lệnh thật.")
    frame = pd.DataFrame(rows)
    lines = ["# Phân tích lịch sử chuẩn hóa · Vnstock/KBS", "", f"Cập nhật: {generated}.", "",
        f"**{len(books)} snapshot / {len(rows)} mã / {days} ngày thực thu**. "
        f"Mẫu mới nhất nhận {anchor.astimezone(LOCAL_TZ).isoformat()}.", "", note, "",
        "| Mã | Bid depth CP | Ask depth CP | Coverage baseline | Coverage giảm depth 50% | Uncovered stress CP |",
        "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['Mã']} | {row['Bid depth (CP)']:,} | {row['Ask depth (CP)']:,} | "
            f"{row['Coverage baseline']} | {row['Coverage giảm depth 50%']} | {row['Uncovered stress (CP)']:,} |")
    lines += ["", "Đơn vị SDK đã được thẩm định thực nghiệm với hệ số 1/1; xem [bằng chứng và giới hạn](kbs_unit_review_2026-10-28.md).",
        "", "Kiểm chứng trên toàn mẫu: [A1/B5 và danh mục giả định](normalized_validation_2026-10-28.json). "
        "Các phép kiểm tra tự động xác nhận số học/invariant, không bảo đảm khớp lệnh thực tế.",
        "", "Mở Streamlit tại http://127.0.0.1:8501, chọn **vnstock → Replay lịch sử**. "
        "Snapshot hiện tại đã cũ; replay dùng mốc nhận đã chọn, không gọi mạng."]
    depth = px.line(pd.DataFrame(curve),x="Thời điểm nhận",y="Độ sâu (CP)",color="Mã",line_dash="Bên",
                    title="Độ sâu ba mức giá thực thu · theo thời điểm nhận REST")
    coverage = px.bar(pd.DataFrame(bars),x="Mã",y="Coverage",color="Kịch bản",barmode="group",
                      title="Lệnh bán giả định 500.000 CP/mã tại mẫu cuối · baseline và giảm depth 50%")
    coverage.update_yaxes(tickformat=".0%")
    content = ("<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Phân tích thanh khoản lịch sử</title><style>body{font:16px system-ui;line-height:1.6;max-width:1200px;margin:32px auto;"
        "padding:0 20px;color:#183047}table{border-collapse:collapse;font-size:14px}td,th{padding:8px;border:1px solid #ddd}"
        ".table{overflow:auto}.note{background:#fff1cd;padding:16px}</style><h1>Phân tích thanh khoản lịch sử</h1>"
        f"<p>{len(books)} snapshot thật · {len(rows)} mã · Cập nhật {html.escape(generated)}</p>"
        f"<p class='note'>{html.escape(note)}</p><p>Mẫu mới nhất nhận: {html.escape(anchor.astimezone(LOCAL_TZ).isoformat())}</p>"
        "<div class='table'>"+frame.to_html(index=False,escape=True)+"</div>"
        +depth.to_html(full_html=False,include_plotlyjs=True)+coverage.to_html(full_html=False,include_plotlyjs=False)
        +"<p>Đơn vị SDK: VND/CP và CP, hệ số chuẩn hóa 1/1. Hồ sơ thẩm định và kiểm chứng nằm cùng thư mục báo cáo.</p></html>")
    stem = Path(args.output_stem)
    stem.parent.mkdir(parents=True,exist_ok=True)
    stem.with_suffix(".md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    stem.with_suffix(".html").write_text(content,encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps({"generated_at_local":generated,"observations":len(books),
        "analysis_anchor_utc":anchor.isoformat(),"hypothetical_sell_quantity_per_symbol":quantity,"rows":rows,
        "note":note},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"observations":len(books),"report":str(stem.with_suffix('.html'))}))


if __name__=="__main__":
    main()
