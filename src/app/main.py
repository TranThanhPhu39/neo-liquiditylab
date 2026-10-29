from datetime import datetime, timezone
from decimal import Decimal
import io
from pathlib import Path
import sys

# Also works with `streamlit run src/app/main.py` before editable installation.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config.settings import Settings
from src.data_pipeline.collector import collect_once
from src.data_pipeline.quality import quality_report
from src.data_pipeline.replay import latest_at
from src.data_pipeline.raw_collection import raw_quality_report, replay_raw_at
from src.data_pipeline.storage import Storage
from src.data_pipeline.validation import freshness, scheduled_session
from src.data_providers import create_provider
from src.finance.execution_simulator import simulate_execution
from src.finance.historical_analytics import historical_frame, historical_summary
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.portfolio_liquidation import liquidate_portfolio
from src.finance.stress_testing import apply_scenario, compare_scenario, reverse_stress
from src.models.portfolio import parse_portfolio
from src.models.scenario import Scenario

st.set_page_config(page_title="NEO LiquidityLab", page_icon="◈", layout="wide")
settings = Settings()
st.markdown("""<style>
.stApp {background: #f5f7fb;}
h1,h2,h3 {color: #142b47;}
[data-testid="stMetric"] {background:white;border:1px solid #e1e6ef;border-radius:12px;padding:16px;}
[data-testid="stSidebar"] {background:#edf1f7;}
</style>""",unsafe_allow_html=True)


def money(value, decimals=0):
    return "Không xác định" if value is None else f"{value:,.{decimals}f}"


def percent(value):
    return "Không xác định" if value is None else f"{value:.1%}"


def plot(fig):
    fig.update_layout(template="plotly_white",paper_bgcolor="rgba(0,0,0,0)",margin=dict(t=35,b=30,l=20,r=20),height=340)
    st.plotly_chart(fig,width="stretch")


def levels_frame(snapshot):
    return pd.DataFrame([{"Bên": side, "Mức":x.level,"Giá (VND/CP)":float(x.price) if x.price is not None else None,
                          "Khối lượng (CP)":x.volume} for side,levels in (("Bid",snapshot.bids),("Ask",snapshot.asks)) for x in levels])


def result_metrics(result):
    columns = st.columns(4)
    for col,(label,value) in zip(columns,[("Bao phủ",percent(result.coverage_ratio)),("Khối lượng được bao phủ",money(result.executed_quantity)),
             ("Chưa bao phủ (CP)",money(result.uncovered_quantity)),("VWAP phần bao phủ",money(result.vwap,4))]):
        col.metric(label,value)
    cols = st.columns(4)
    for col,(label,value) in zip(cols,[("Gross phần bao phủ (VND)",money(result.gross_value)),("Chi phí so benchmark (VND)",money(result.book_walking_cost)),
          ("Chi phí (bps)",money(result.cost_bps,2)),("Net phần bao phủ (VND)",money(result.net_value))]):
        col.metric(label,value)
    st.caption(f"Phí giả định: {money(result.fees)} VND · Thuế bán giả định: {money(result.sell_tax)} VND. "
               "Chi phí và net chỉ áp dụng cho khối lượng được bao phủ. Với chiều mua, net là tổng tiền chi.")


st.sidebar.markdown("## ◈ NEO LiquidityLab")
st.sidebar.caption("PRE-TRADE • A1 + B5")
sources = ["demo","vnstock","dnse","neo"]
source = st.sidebar.selectbox("Nguồn dữ liệu",sources,index=sources.index(settings.data_source) if settings.data_source in sources else 0)
mode = st.sidebar.radio("Chế độ",["Snapshot","Replay lịch sử"])
page = st.sidebar.radio("Màn hình",["Thanh khoản","Mô phỏng thực hiện lệnh","Stress danh mục"])
if source == "dnse":
    from src.app.dnse_history import render
    render(settings, mode, page)
    st.stop()
provider = create_provider(source)
label = provider.get_provider_info()["label"]
st.sidebar.info(label)
st.sidebar.caption("NEO API: chưa tích hợp / chưa kiểm thử kết nối thật.")
st.title("NEO LiquidityLab")
st.caption("Mô phỏng chi phí thực hiện lệnh & sức chịu đựng thanh khoản danh mục cổ phiếu Việt Nam")

if "demo_snapshots" not in st.session_state:
    st.session_state.demo_snapshots = create_provider("demo").get_snapshots(["FPT","HPG","ACB"])

try:
    with Storage(settings.database_path) as db:
        if source == "demo" and st.sidebar.button("Lưu fixture demo vào SQLite"):
            st.sidebar.success(str(collect_once(provider,db,["FPT","HPG","ACB"])))
        if source == "vnstock" and st.sidebar.button("Thu một snapshot Vnstock",disabled=not provider.health_check()["ready"]):
            try:
                st.sidebar.success(str(collect_once(provider,db,["FPT","HPG","ACB"])))
            except Exception as error:
                st.sidebar.error("DISCONNECTED · " + str(error))
                st.session_state["vnstock_disconnected"] = True
            else:
                st.session_state["vnstock_disconnected"] = False
        history = db.query(provider=source)
        report = quality_report(db,provider=source)
        # Bulk collection history can contain millions of raw rows. Normalized views need none of them.
        raw_batches = db.raw_batches() if source=="vnstock" and not history else []
        raw_report = raw_quality_report(db) if raw_batches else None
except Exception as error:
    st.error(f"Không đọc được SQLite ({type(error).__name__}). Kiểm tra đường dẫn/quyền truy cập hoặc database bị khóa.")
    history, report = [], {"snapshots":0}
    raw_batches,raw_report = [],None

snapshots = st.session_state.demo_snapshots if source == "demo" else history
if source=="vnstock":
    automation_status=ROOT/"report/automation/latest.json"
    if automation_status.exists():
        try:
            status_data=__import__("json").loads(automation_status.read_text(encoding="utf-8"))
            with st.sidebar.expander("Thu tự động toàn thị trường"):
                st.write("Trạng thái: "+status_data.get("state","UNKNOWN"))
                st.caption(f"Danh mục: {status_data.get('plan',{}).get('symbols','—')} cổ phiếu HOSE/HNX/UPCoM. "
                           "A1/B5 hiện chỉ dùng các mã đã thẩm định đơn vị: FPT, HPG, ACB.")
                st.caption("Cập nhật: "+status_data.get("updated_at_local","—"))
        except (OSError,ValueError):
            st.sidebar.caption("Chưa đọc được trạng thái lịch thu tự động.")
analysis_time = datetime.now(timezone.utc)
if raw_report and not history:
    st.warning("VNSTOCK DATA · RAW ONLY · Chưa xác minh đơn vị khối lượng; A1/B5 chưa sử dụng dữ liệu này.")
    st.json(raw_report)
    cutoff = datetime.fromisoformat(raw_batches[-1]["received_at_utc"])
    if mode=="Replay lịch sử" and len(raw_batches)>1:
        points = [datetime.fromisoformat(b["received_at_utc"]) for b in raw_batches]
        cutoff = st.select_slider("Thời điểm nhận raw để replay (UTC)",options=points,value=points[-1],format_func=lambda x:x.isoformat())
    st.caption("Các cột bid_vol/ask_vol giữ nguyên giá trị nguồn, chưa gán đơn vị CP. Timestamp nguồn chưa có; chỉ biết thời điểm nhận.")
    raw_frame = pd.DataFrame(replay_raw_at(raw_batches,cutoff).values())
    st.dataframe(raw_frame,hide_index=True,width="stretch")
    st.download_button("Tải mẫu raw đang xem (CSV)",raw_frame.to_csv(index=False),"raw_unverified.csv","text/csv")
    st.info("A1/B5 và danh mục cần dữ liệu chuẩn hóa sau xác minh đơn vị. Có thể chọn Demo để thử lõi tài chính.")
    st.stop()
if mode == "Replay lịch sử":
    if not history:
        st.info("NO DATA · Chưa có lịch sử cho nguồn này. Có thể lưu fixture demo để thử replay offline.")
        st.stop()
    # Replay cut uses actual arrival timestamps, avoiding future/late events.
    points = sorted({s.collected_at_utc for s in history})
    analysis_time = st.sidebar.select_slider("Thời điểm replay (UTC)",options=points,value=points[-1],format_func=lambda x:x.isoformat()) if len(points)>1 else points[0]
    snapshots = list(latest_at(history,analysis_time).values())
    st.info(f"REPLAY · Dữ liệu đã lưu đến {analysis_time.isoformat()}; không gọi mạng.")
elif source != "demo":
    # A late event cannot replace a newer event already observed.
    snapshots = list(latest_at(history,analysis_time).values())

if source == "neo":
    st.warning("DISCONNECTED · NEO chưa cấu hình transport/auth/mapping. Không có dữ liệu NEO được tạo giả.")
if st.session_state.get("vnstock_disconnected") and source == "vnstock":
    st.warning("DISCONNECTED · Lần thu gần nhất thất bại. Dữ liệu trong database chỉ là snapshot đã lưu.")
if not snapshots:
    st.info("NO DATA · Chọn Demo để chạy offline hoặc cấu hình collector cho nguồn được phép.")
    st.stop()

books = {s.symbol:s for s in snapshots}
symbol = st.sidebar.selectbox("Mã cổ phiếu",sorted(books),index=sorted(books).index("HPG") if "HPG" in books else 0)
book = books[symbol]
st.markdown(f"**{label}** · **{symbol}** · {book.exchange}")
status = freshness(book,analysis_time,settings.stale_seconds)
st.caption(f"Nhận dữ liệu: {book.received_at_local.isoformat()} · Timestamp nguồn: "
           f"{book.source_timestamp.isoformat() if book.source_timestamp else 'KHÔNG CÓ'} · Asia/Ho_Chi_Minh")
if source == "demo":
    st.warning("DEMO / SYNTHETIC DATA · Giá và khối lượng giả lập cố định ngày 20/10/2026, chỉ dùng minh họa và kiểm thử.")
if status == "STALE DATA":
    st.warning("STALE DATA · Snapshot cũ. Mô phỏng trên dữ liệu cũ phải được chủ động bật ở dưới.")
elif status == "SOURCE TIME UNKNOWN":
    st.warning("Không có timestamp nguồn. Thời điểm nhận REST không chứng minh giá đang realtime.")
if book.market_session == "UNKNOWN" or scheduled_session(analysis_time) != "SCHEDULED_CONTINUOUS":
    st.caption("Trạng thái phiên nguồn chưa xác minh hoặc đang ngoài cửa sổ thu thập tham khảo; không hiển thị LIVE.")
if book.data_quality_status != "VALID":
    st.warning(f"{book.data_quality_status} DATA · " + ", ".join(book.issues))

if page == "Thanh khoản":
    st.subheader("Thanh khoản tại thời điểm quan sát")
    try:
        m = liquidity_metrics(book)
    except ValueError as error:
        st.error(str(error))
        st.stop()
    cols = st.columns(4)
    for col,(name,value) in zip(cols,[("Relative spread",percent(m["relative_spread"])),("Bid depth 3 (CP)",money(m["bid_depth"])),
                                    ("Ask depth 3 (CP)",money(m["ask_depth"])),("OBI",money(m["obi"],4))]):
        col.metric(name,value)
    st.caption("Độ sâu là tổng các mức hợp lệ quan sát được; PARTIAL DATA có thể thiếu mức. OBI không phải tín hiệu mua/bán.")
    left,right = st.columns([1.2,1])
    with left:
        plot(px.bar(levels_frame(book),x="Giá (VND/CP)",y="Khối lượng (CP)",color="Bên",barmode="group",title="Ba mức giá đang quan sát",color_discrete_map={"Bid":"#247b87","Ask":"#d58a43"}))
    with right:
        st.dataframe(levels_frame(book),hide_index=True,width="stretch")
    st.subheader("Lịch sử đã lưu & chất lượng dữ liệu")
    selected_history = [s for s in history if s.symbol == symbol]
    if selected_history:
        date_options = sorted({s.received_at_local.date() for s in selected_history})
        chosen_dates = st.multiselect("Ngày thu thập (giờ Việt Nam)",date_options,default=date_options)
        selected_history = [s for s in selected_history if s.received_at_local.date() in chosen_dates]
        frame = historical_frame(selected_history)
        st.json(historical_summary(frame))
        if len(frame)>1:
            left,right = st.columns(2)
            with left:
                plot(px.line(frame,x="time",y="relative_spread",title="Relative spread theo thời gian"))
            with right:
                plot(px.line(frame,x="time",y=["bid_depth","ask_depth"],title="Độ sâu theo thời gian (CP)"))
            cols=st.columns(2)
            with cols[0]:
                plot(px.histogram(frame,x="coverage",title="Phân phối coverage · lệnh cố định 3.000 CP"))
            with cols[1]:
                plot(px.histogram(frame,x="cost",title="Phân phối chi phí phần bao phủ · VND"))
        else:
            st.info("Cần ít nhất hai snapshot để vẽ chuỗi thời gian; không sinh thêm lịch sử.")
    else:
        st.info("Chưa lưu lịch sử cho mã này. Không có thống kê lịch sử quan sát thật.")
    with st.expander("Báo cáo chất lượng và xuất dữ liệu"):
        st.json(report)
        data = pd.DataFrame([s.to_record() for s in history]).to_csv(index=False).encode("utf-8-sig")
        st.download_button("Tải snapshot nguồn đang chọn (CSV)",data,"snapshots.csv","text/csv")
        st.download_button("Tải báo cáo chất lượng (JSON)",__import__("json").dumps(report,ensure_ascii=False,indent=2),"quality.json")

elif page == "Mô phỏng thực hiện lệnh":
    st.subheader("A1 · Mô phỏng quét ba mức giá")
    allow_old = st.checkbox("Cho phép mô phỏng snapshot cũ (giữ cảnh báo)",value=source=="demo")
    if status == "STALE DATA" and not allow_old:
        st.info("Bật lựa chọn trên để phân tích snapshot cũ.")
        st.stop()
    cols = st.columns(4)
    side = cols[0].selectbox("Chiều lệnh",["sell","buy"],format_func=lambda x:"Bán vào Bid" if x=="sell" else "Mua vào Ask")
    quantity = cols[1].number_input("Số lượng (CP)",min_value=1,value=3000,step=100)
    benchmark = cols[2].selectbox("Benchmark",["best","mid"],format_func=lambda x:"Best Bid / Ask" if x=="best" else "Mid-price")
    fee = cols[3].number_input("Phí giả định (%)",min_value=0.0,max_value=50.0,value=0.0,step=0.01)
    tax = st.number_input("Thuế bán giả định (%)",min_value=0.0,max_value=50.0,value=0.0,step=0.01)
    try:
        result = simulate_execution(book,int(quantity),side,Decimal(str(fee))/100,Decimal(str(tax))/100,benchmark)
        result_metrics(result)
        if result.uncovered_quantity:
            st.warning(f"{money(result.uncovered_quantity)} CP chưa được bao phủ. Không có giá thực hiện cho phần này.")
        st.dataframe(pd.DataFrame([{"Mức":f.level,"Giá":str(f.price),"Khối lượng":f.quantity,"Giá trị VND":str(f.price*f.quantity)} for f in result.fills]),hide_index=True,width="stretch")
        sizes = sorted(set([1,100,500,1000,int(quantity),int(quantity)*2] + [int(quantity*i/10) or 1 for i in range(1,21)]))
        curve = [simulate_execution(book,q,side,benchmark=benchmark) for q in sizes]
        frame = pd.DataFrame({"Số lượng yêu cầu (CP)":sizes,"Chi phí phần bao phủ (VND)":[float(r.book_walking_cost) if r.book_walking_cost is not None else None for r in curve],"Coverage":[float(r.coverage_ratio) for r in curve]})
        left,right = st.columns(2)
        with left:
            plot(px.line(frame,x="Số lượng yêu cầu (CP)",y="Chi phí phần bao phủ (VND)",title="Chi phí theo quy mô lệnh"))
        with right:
            plot(px.line(frame,x="Số lượng yêu cầu (CP)",y="Coverage",title="Tỷ lệ bao phủ theo quy mô lệnh"))
        for warning in result.warnings:
            st.caption(warning)
    except (ValueError,ArithmeticError) as error:
        st.error(str(error))

else:
    st.subheader("B5 · Stress thanh khoản danh mục")
    st.info("Manual Portfolio – Simulated Holdings · Số lượng do người dùng nhập giả định.")
    local_portfolio = ROOT/"data/portfolio.csv"
    if "portfolio_frame" not in st.session_state:
        try:
            st.session_state.portfolio_frame = pd.read_csv(local_portfolio if local_portfolio.exists() else ROOT/"data/sample/portfolio.csv")
        except Exception:
            st.warning("Không đọc được danh mục cục bộ. Đang mở danh mục mẫu giả định.")
            st.session_state.portfolio_frame = pd.read_csv(ROOT/"data/sample/portfolio.csv")
    upload = st.file_uploader("Nhập CSV (symbol,quantity)",type=["csv"])
    if upload and st.button("Áp dụng CSV"):
        try:
            positions = parse_portfolio(upload.getvalue().decode("utf-8-sig"))
            st.session_state.portfolio_frame = pd.DataFrame([{"symbol":p.symbol,"quantity":p.quantity} for p in positions])
        except Exception:
            st.error("CSV không hợp lệ: cần symbol,quantity; số lượng nguyên dương; không trùng mã.")
    edited = st.data_editor(st.session_state.portfolio_frame,num_rows="dynamic",hide_index=True,width="stretch",key="portfolio_editor")
    try:
        positions = parse_portfolio(edited.to_csv(index=False))
        if st.button("Lưu danh mục cục bộ"):
            local_portfolio.parent.mkdir(parents=True,exist_ok=True)
            edited.to_csv(local_portfolio,index=False)
            st.success("Đã lưu data/portfolio.csv")
        st.download_button("Tải danh mục CSV",edited.to_csv(index=False),"portfolio.csv","text/csv")
        focus = st.selectbox("Phạm vi",["Toàn danh mục"]+[p.symbol for p in positions])
        if focus != "Toàn danh mục":
            positions = [p for p in positions if p.symbol == focus]
        cols = st.columns(3)
        fraction = cols[0].slider("Tỷ lệ bán danh mục (%)",0,100,100)
        shock = cols[1].slider("Giảm độ sâu mua (%)",0,90,50)
        price_shock = cols[2].slider("Cú sốc giá giả định (%)",-50,50,0)
        allow_stale = st.checkbox("Cho phép snapshot cũ trong phân tích danh mục",value=source=="demo")
        scenario = Scenario(Decimal(shock)/100,Decimal(price_shock)/100,"Điều chỉnh người dùng")
        common = dict(fraction=Decimal(fraction)/100,now=analysis_time,stale_seconds=settings.stale_seconds,
                      max_skew_seconds=settings.max_skew_seconds,allow_stale=allow_stale)
        baseline = liquidate_portfolio(positions,books,**common)
        stressed = liquidate_portfolio(positions,{s:apply_scenario(b,scenario) for s,b in books.items()},**common)
        cols = st.columns(2)
        for col,name,r in zip(cols,["Baseline","Stressed"],[baseline,stressed]):
            with col:
                st.markdown(f"### {name}")
                st.metric("Coverage phần đủ dữ liệu",percent(r["coverage"]))
                st.metric("Gross phần bao phủ (VND)",money(r["gross_value"]))
                st.metric("Chưa bao phủ (CP)",money(r["uncovered"]))
                st.metric("Chi phí phần tính được (VND)",money(r["cost"]))
                st.caption(f"Yêu cầu: {money(r['requested'])} CP · Đủ dữ liệu: {money(r['analyzed_quantity'])} CP · "
                           f"Bị loại: {money(r['excluded_quantity'])} CP · Giá trị tham chiếu phần có giá: {money(r['reference_value'])} VND")
        if baseline["exclusions"]:
            st.warning("Các mã bị loại khỏi mẫu số coverage và tổng tiền (không được coi là đã bán):")
            st.json(baseline["exclusions"])
        if baseline["cost_undefined_symbols"] or baseline["reference_missing"]:
            st.warning("Thiếu benchmark/giá tham chiếu: tổng chỉ cộng phần tính được.")
        rows = []
        for name,r in (("Baseline",baseline),("Stressed",stressed)):
            rows += [dict(row,scenario=name) for row in r["rows"]]
        frame = pd.DataFrame(rows)
        if not frame.empty:
            left,right = st.columns(2)
            with left:
                plot(px.bar(frame,x="symbol",y="coverage",color="scenario",barmode="group",title="Bao phủ theo mã"))
            with right:
                plot(px.bar(frame,x="symbol",y="uncovered",color="scenario",barmode="group",title="Đóng góp vào phần chưa bao phủ (CP)"))
            st.dataframe(frame,hide_index=True,width="stretch")
            pivot=frame.pivot(index="symbol",columns="scenario",values="coverage")
            drop=(pivot["Baseline"]-pivot["Stressed"]).sort_values(ascending=False)
            st.caption(f"Mã giảm coverage lớn nhất: {drop.index[0]} ({drop.iloc[0]:.1%}). Tổng CP giữa các mã chỉ là thống kê số lượng, không phải tỷ trọng giá trị.")
        with st.expander("Reverse stress trên mã đang chọn"):
            threshold = st.slider("Coverage tối thiểu mong muốn (%)",1,100,70)
            reverse_qty = st.number_input("Số lượng reverse stress (CP)",min_value=1,value=4000)
            if st.button("Tìm ngưỡng suy giảm"):
                answer = reverse_stress(book,int(reverse_qty),Decimal(threshold)/100)
                st.write("Không rơi dưới ngưỡng trong khoảng 0–90%." if answer is None else f"Mức giảm đầu tiên trên lưới 0,01%: {answer:.2%}")
    except (ValueError,ArithmeticError,KeyError) as error:
        st.error("Không thể phân tích danh mục: " + str(error))
    st.caption("Giảm độ sâu áp dụng riêng từng mức Bid, làm tròn xuống cổ phiếu nguyên. Cú sốc giá nhân mọi giá với (1 + shock); "
               "không làm tròn bước giá và không áp dụng biên độ sàn. Đây là phép nhạy cảm toán học, không phải lệnh hợp lệ trên sàn.")

with st.expander("Phương pháp & giới hạn"):
    st.markdown("""Ba mức giá chỉ đại diện tại thời điểm quan sát; không bao gồm toàn bộ sổ lệnh, lệnh mới/hủy hay ưu tiên hàng đợi.
    Kết quả không bảo đảm khớp lệnh. Chi phí quét sổ không phải đầy đủ market impact. Stress là giả định, không dự báo xác suất hay thời gian thanh lý.
    Phí/thuế do người dùng nhập, mặc định 0; không đại diện biểu phí VPBankS. Dữ liệu lịch sử phụ thuộc mẫu thực thu.
    Không có chức năng gửi lệnh thật. NEO chưa tích hợp nên MVP chưa đủ tiêu chí NEO API cốt lõi của đồ án cuối kỳ.""")
