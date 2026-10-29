"""Offline DNSE screens, separate from live collection and arrival-time replay."""
from datetime import datetime
from decimal import Decimal
import json
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.finance.execution_simulator import simulate_execution
from src.finance.historical_analytics import historical_frame
from src.finance.liquidity_metrics import liquidity_metrics
from src.finance.portfolio_liquidation import liquidate_portfolio
from src.finance.stress_testing import apply_scenario, reverse_stress
from src.models.orderbook import LOCAL_TZ
from src.models.portfolio import parse_portfolio
from src.models.scenario import Scenario


def number(value, decimals=0):
    return 'Chưa xác định' if value is None else f'{value:,.{decimals}f}'


def chart(fig):
    fig.update_layout(template='plotly_white', height=330, margin=dict(t=40,b=25,l=10,r=10))
    st.plotly_chart(fig, width='stretch')


def execution_metrics(result):
    columns = st.columns(4)
    for col, (name, value) in zip(columns, [
        ('Bao phủ', f'{result.coverage_ratio:.1%}'), ('VWAP phần bao phủ', number(result.vwap, 2)),
        ('Chi phí so benchmark (VND)', number(result.book_walking_cost)),
        ('Chưa bao phủ (CP)', number(result.uncovered_quantity))]):
        col.metric(name, value)
    st.caption(f'Gross phần bao phủ: {number(result.gross_value)} VND · '
               f'Chi phí: {number(result.cost_bps, 2)} bps · Net: {number(result.net_value)} VND. '
               'Phí và thuế giả định trong màn hình này bằng 0; chỉ tính phần được bao phủ.')


def execution_page(book, eligible):
    st.subheader('A1 · Mô phỏng quét ba mức giá')
    if not eligible:
        st.info('A1 đang khóa cho snapshot này: cần đơn vị đã xác minh, sổ lệnh hợp lệ và nằm trong phiên liên tục.')
        return
    if not st.checkbox('Cho phép phân tích snapshot lịch sử (A1)', key='dnse_allow_a1'):
        st.info('Bật lựa chọn trên để mô phỏng dữ liệu lịch sử.')
        return
    left, right = st.columns(2)
    side = left.selectbox('Chiều lệnh', ['sell', 'buy'], key='dnse_side')
    quantity = right.number_input('Số lượng (CP)', min_value=1, value=150000, step=100, key='dnse_qty')
    try:
        result = simulate_execution(book, int(quantity), side=side)
        execution_metrics(result)
        st.dataframe(pd.DataFrame([dict(level=f.level, price_vnd=str(f.price), quantity=f.quantity)
                                   for f in result.fills]), hide_index=True, width='stretch')
        sizes = sorted({1, 100, int(quantity), *[max(1, int(quantity*i/10)) for i in range(1, 21)]})
        results = [simulate_execution(book, q, side=side) for q in sizes]
        frame = pd.DataFrame({'quantity': sizes, 'coverage': [float(r.coverage_ratio) for r in results],
                              'cost_vnd': [float(r.book_walking_cost) if r.book_walking_cost is not None else None for r in results]})
        chart(px.line(frame, x='quantity', y='cost_vnd', title='Chi phí phần bao phủ theo quy mô lệnh'))
        chart(px.line(frame, x='quantity', y='coverage', title='Coverage theo quy mô lệnh'))
    except ValueError as error:
        st.error(str(error))


def portfolio_page(store, day, at, symbol, settings):
    st.subheader('B5 · Stress thanh khoản danh mục')
    st.info('Manual Portfolio – Simulated Holdings · Danh mục giả định; dữ liệu DNSE lịch sử. '
            'Mã chưa xác minh đơn vị sẽ được báo riêng trong phần loại trừ.')
    text = st.text_area('Danh mục CSV (symbol,quantity)', 'symbol,quantity\nACB,150000', key='dnse_portfolio')
    shock = st.slider('Giảm độ sâu mua (%)', 0, 90, 50, key='dnse_shock')
    if not st.checkbox('Cho phép phân tích snapshot lịch sử (B5)', key='dnse_allow_b5'):
        return
    try:
        positions = parse_portfolio(text)
        records = store.reconstruct_at([p.symbol for p in positions], day, at)
        books = {s: snapshot_from_record(r) for s, r in records.items()}
        common = dict(now=at, allow_stale=True, max_skew_seconds=settings.max_skew_seconds)
        baseline = liquidate_portfolio(positions, books, **common)
        stressed_books = {s: apply_scenario(b, Scenario(Decimal(shock)/100)) for s,b in books.items()}
        stressed = liquidate_portfolio(positions, stressed_books, **common)
        columns = st.columns(2)
        for col, name, result in zip(columns, ['Baseline','Stressed'], [baseline,stressed]):
            with col:
                st.markdown('### ' + name)
                st.metric('Coverage phần đủ dữ liệu', 'Chưa xác định' if result['coverage'] is None else f"{result['coverage']:.1%}")
                st.metric('Chi phí phần tính được (VND)', number(result['cost']))
                st.metric('Chưa bao phủ (CP)', number(result['uncovered']))
                st.caption(f"Đủ dữ liệu: {result['analyzed_quantity']:,} CP · Bị loại: {result['excluded_quantity']:,} CP")
        if baseline['exclusions']:
            st.warning('Các mã sau bị loại khỏi mẫu số coverage và tổng tiền:')
            st.json(baseline['exclusions'])
        if baseline['reference_missing']:
            st.caption('Endpoint sổ lệnh không cung cấp giá tham chiếu/giá khớp; không tính tổng giá trị danh mục từ các trường thiếu.')
        frame = pd.DataFrame([dict(row,scenario=name) for name,r in [('Baseline',baseline),('Stressed',stressed)] for row in r['rows']])
        if not frame.empty:
            chart(px.bar(frame, x='symbol', y='coverage', color='scenario', barmode='group', title='Bao phủ theo mã'))
            st.dataframe(frame, hide_index=True, width='stretch')
        if symbol in books:
            with st.expander('Reverse stress trên mã đang chọn'):
                threshold = st.slider('Coverage tối thiểu (%)',1,100,70,key='dnse_threshold')
                quantity = st.number_input('Số lượng reverse stress (CP)', min_value=1, value=150000,key='dnse_reverse_qty')
                if st.button('Tìm ngưỡng suy giảm',key='dnse_reverse'):
                    result = reverse_stress(books[symbol], int(quantity), Decimal(threshold)/100)
                    st.write('Không rơi dưới ngưỡng trong khoảng 0–90%.' if result is None else f'Giảm độ sâu: {result:.2%}')
        st.caption('Stress giảm khối lượng từng mức Bid và làm tròn xuống cổ phiếu nguyên. Không dự báo thời gian thanh lý hay xác suất khủng hoảng.')
    except (ValueError, ArithmeticError) as error:
        st.error(str(error))


def liquidity_page(store, day, at, record, book):
    st.subheader('Thanh khoản tại thời điểm quan sát')
    try:
        metrics = liquidity_metrics(book)
        cols = st.columns(4)
        for col, (name, value) in zip(cols, [('Bid depth 3 (CP)', record['bid_observed_depth_shares']),
            ('Ask depth 3 (CP)', record['ask_observed_depth_shares']), ('Relative spread', metrics['relative_spread']),
            ('OBI', metrics['obi'])]):
            col.metric(name, number(value,4 if name in ('Relative spread','OBI') else 0))
    except ValueError as error:
        st.warning(str(error))
    levels = pd.DataFrame([{'side':side,'level':x.level,'price_vnd':float(x.price) if x.price is not None else None,
                            'quantity_shares':x.volume} for side,values in [('Bid',book.bids),('Ask',book.asks)] for x in values])
    st.caption('Giá VND/CP; khối lượng CP. Trường thiếu giữ trống. Độ sâu cộng từ các mức quan sát được. '
               'Với ASSUMED_SHARED_SCHEMA, các giá trị quy đổi vẫn chưa được xác minh trực tiếp cho mã này.')
    st.dataframe(levels, hide_index=True, width='stretch')
    chart(px.bar(levels,x='price_vnd',y='quantity_shares',color='side',barmode='group', title='Ba mức giá quan sát được'))
    samples = store.sampled_records(book.symbol, day, at)
    frame = historical_frame([snapshot_from_record(r) for r in samples])
    st.subheader('Lịch sử theo thời gian nguồn')
    st.caption(f'{len(samples):,} điểm đại diện: snapshot cuối mỗi khoảng 144 giây, tối đa 600 điểm/ngày. '
               'Chỉ lấy điểm đến thời gian đã chọn; các biểu đồ này là mẫu, không phải thống kê toàn bộ phiên.')
    if len(frame)>1:
        chart(px.line(frame,x='time',y=['bid_depth','ask_depth'],title='Độ sâu quan sát được (CP)'))
        chart(px.line(frame,x='time',y='relative_spread',title='Relative spread theo thời gian'))
        eligible = frame.dropna(subset=['coverage'])
        if not eligible.empty:
            chart(px.histogram(eligible,x='coverage',title='Coverage trên mẫu · lệnh bán giả định 3.000 CP'))
    st.download_button('Tải các điểm mẫu đang xem (CSV)', pd.DataFrame(samples).to_csv(index=False).encode('utf-8-sig'),
                       f'dnse_{book.symbol}_{day}_sample.csv','text/csv')


def render(settings, mode, page):
    st.sidebar.info('DNSE · Kho lịch sử chuẩn hóa · OFFLINE')
    st.title('NEO LiquidityLab · DNSE lịch sử')
    st.info('TÁI DỰNG LỊCH SỬ · Dữ liệu được tải sau phiên; đây không phải dữ liệu đã nhận tại thời điểm thị trường đó. '
            'Không gọi API khi xem Dashboard. NEO API chưa tích hợp.')
    store = DNSEHistory(settings.dnse_database_path)
    try:
        dates = store.dates()
        if not dates:
            st.info('NO DATA · Kho DNSE chưa có snapshot chuẩn hóa.')
            return
        day = st.sidebar.selectbox('Ngày dữ liệu nguồn (Việt Nam)', dates, index=len(dates)-1, key='dnse_day')
        catalog = store.catalog(day)
        symbols = sorted(catalog)
        if not symbols:
            st.info('NO DATA · Ngày này chưa có danh mục nguồn.')
            return
        symbol = st.sidebar.selectbox('Mã cổ phiếu', symbols, index=symbols.index('ACB') if 'ACB' in symbols else 0,key='dnse_symbol')
        window = store.window(symbol, day)
        st.caption(f"{symbol} · {catalog[symbol]['exchange']} · {window['snapshots']:,} snapshot chuẩn hóa trong ngày đã chọn.")
        if not window['snapshots']:
            st.info('NO DATA · API không trả snapshot trong khoảng đã thu; chưa thể suy ra sổ lệnh bằng 0.')
            st.json(catalog[symbol])
            checks = store.manual_checks(day,symbol)
            if checks:
                st.caption('Đối chiếu thủ công tại thời điểm kiểm tra; không thay thế lịch sử sổ lệnh:')
                st.json(checks)
            return
        latest = datetime.fromisoformat(window['last_source_time']).astimezone(LOCAL_TZ)
        at = latest
        if mode == 'Replay lịch sử':
            clock = st.sidebar.time_input('Đến hết phút nguồn (giờ Việt Nam)', latest.time().replace(second=0,microsecond=0), step=60,
                                           key=f'dnse_time_{day}')
            at = datetime.combine(datetime.fromisoformat(day).date(), clock, LOCAL_TZ).replace(second=59,microsecond=999999)
        st.caption(f'Khoảng nguồn đã lưu: {window["first_source_time"]} → {window["last_source_time"]} (UTC). '
                   f'Thời điểm đang chọn: {at.isoformat()}. Snapshot chọn bản cuối không muộn hơn thời điểm này.')
        records = store.reconstruct_at([symbol],day,at)
        if symbol not in records:
            st.info('NO DATA · Chưa có snapshot trước thời điểm đã chọn; không dùng snapshot tương lai.')
            return
        record = records[symbol]
        book = snapshot_from_record(record)
        st.markdown(f'**{symbol} · {record["unit_verification_status"]} · {book.market_session}**')
        st.caption(f'Thời gian nguồn: {book.source_timestamp.astimezone(LOCAL_TZ).isoformat()} · '
                   f'Thực nhận khi tải: {book.received_at_local.isoformat()} · Chất lượng: {book.data_quality_status}')
        if record['unit_verification_status']=='ASSUMED_SHARED_SCHEMA':
            st.warning('Đơn vị mới áp theo schema chung, chưa xác minh trực tiếp cho mã này. A1/B5 đang khóa tính toán cho mã này.')
        elif not record['financial_eligible']:
            st.warning('Snapshot này nằm ngoài phiên liên tục hoặc chưa đủ chất lượng để tính A1/B5. Chọn Replay để xem thời điểm khác.')
        if page == 'Thanh khoản':
            liquidity_page(store,day,at,record,book)
        elif page == 'Mô phỏng thực hiện lệnh':
            execution_page(book,record['financial_eligible'])
        else:
            portfolio_page(store,day,at,symbol,settings)
        with st.expander('Chất lượng & snapshot đang xem'):
            st.json(dict(window=window, archive=catalog[symbol], snapshot=record))
            st.download_button('Tải snapshot đang xem (JSON)',json.dumps(record,ensure_ascii=False,indent=2),
                               f'dnse_{symbol}_{day}.json','application/json')
        st.caption('Ba mức giá không mô tả toàn bộ sổ lệnh hay ưu tiên hàng đợi. Kết quả không bảo đảm khớp lệnh. Không gửi lệnh thật.')
    except (sqlite3.Error, OSError, ValueError, KeyError) as error:
        st.error(f'Không đọc được kho DNSE ({type(error).__name__}). Kiểm tra đường dẫn DNSE_DATABASE_PATH và hoàn tất chuẩn hóa/audit trước khi mở lại.')
