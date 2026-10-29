"""Offline descriptive reports for every archived equity; no unverified A1/B5."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import time
from decimal import Decimal

import pandas as pd
from plotly.offline import get_plotlyjs

from config.settings import ROOT
from src.data_pipeline.dnse_history import DNSEHistory
from src.finance.dnse_market_analysis import inventory, analyse_symbol, SOURCE_METRICS
from src.models.orderbook import LOCAL_TZ

VERSION='dnse-descriptive-market-v1'
STATE_LABELS={
    'DESCRIPTIVE_COMPARISON_READY':'Có mẫu chung để so sánh',
    'NO_COMMON_FRESH_COMPLETE_MINUTES':'Có bản ghi, chưa đủ phút chung',
    'NO_QUOTES_IN_SELECTED_ARCHIVES':'Chưa có snapshot trong kho đã chọn'}
FINANCE_LABELS={
    'SEE_EXISTING_VERIFIED_ACB_REPORT':'ACB đã xác minh · xem báo cáo mô phỏng riêng',
    'BLOCKED_UNVERIFIED_UNITS':'Chưa tính · đơn vị chưa xác minh'}
REASON_LABELS={
    'NO_EVENT_BEFORE_CUTOFF':'Chưa có sự kiện trước mốc',
    'NOT_COLLECTED_THROUGH_CUTOFF':'Kho chưa thu đến thời điểm này',
    'API_EMPTY_RESPONSE':'API trả rỗng trong khoảng đã thu',
    'NO_NORMALIZED_RECORDS':'Chưa có bản ghi chuẩn hóa',
    'OUTSIDE_SELECTED_CONTINUOUS_SESSION':'Sự kiện thuộc giai đoạn khác',
    'STALE_OR_FUTURE':'Sự kiện quá cũ hoặc muộn hơn mốc',
    'INVALID_BOOK':'Sổ lệnh không hợp lệ',
    'SOURCE_EXCHANGE_MISMATCH':'Sàn nguồn không khớp danh mục',
    'INCOMPLETE_TOP3':'Chưa đủ ba mức hợp lệ mỗi phía'}
METHOD=('Mẫu cuối phút theo giờ liên tục từng sàn: HOSE 09:15–11:30/13:00–14:30; '
        'HNX 09:00–11:30/13:00–14:30; UPCOM 09:00–11:30/13:00–15:00. '
        'Lấy thông điệp cuối không muộn hơn mốc, tuổi nguồn tối đa 60 giây, đủ ba mức hợp lệ mỗi phía. '
        'So sánh ngày trên tập phút đủ điều kiện chung, không nội suy qua nghỉ trưa hoặc ngày khác. '
        'Độ phủ mẫu đo tiêu chí dữ liệu, không phải tần suất khớp lệnh hay xếp hạng thanh khoản.')
UNIT_NOTE=('Độ sâu dùng nguyên đơn vị quantity nguồn DNSE, được truy lại bằng phép đảo hệ số chuẩn hóa đã audit; '
           'không chứng nhận các giá trị này là CP. Spread và OBI là tỷ lệ từ cùng schema nguồn. '
           'Báo cáo không tính chi phí, VWAP, proceeds hay stress cho mã chưa xác minh đơn vị. '
           'Phản hồi rỗng/điểm thiếu giữ là chưa có dữ liệu, không tạo sổ lệnh bằng 0.')


def display(value, decimals=2):
    return 'Chưa có mẫu' if value is None else f'{Decimal(str(value)):,.{decimals}f}'


def encoded(value):
    return json.dumps(value,ensure_ascii=False,default=str).replace('<','\\u003c')


def write_symbol(report, folder):
    symbol=report['symbol']
    if not re.fullmatch('[A-Z0-9]{1,12}',symbol):
        raise ValueError('UNSAFE_SYMBOL_FILENAME')
    packet=folder/'symbols'/(symbol+'.json.gz')
    with gzip.open(packet,'wt',encoding='utf-8',compresslevel=5) as stream:
        json.dump(report,stream,ensure_ascii=False,default=str)
    rows=[]
    for group in report['summaries']:
        med=group['matched_medians']
        rows.append({'Ngày':group['date'],'Phiên':group['session'],'Trong danh mục':group['catalog_present'],
            'Mốc phút':group['grid_points'],'Mẫu đủ tươi/đủ mức':group['eligible_points'],'Phút chung':group['matched_points'],
            'Median spread (bps)':display(med['spread_bps']), 'Median OBI':display(med['obi'],4),
            'Median dư mua, đơn vị nguồn':display(med['bid_depth_source']),
            'Median dư bán, đơn vị nguồn':display(med['ask_depth_source']),
            'Lý do loại trừ':'; '.join(REASON_LABELS.get(k,k)+': '+str(v) for k,v in group['exclusion_reasons'].items()) or 'Không có'})
    table=pd.DataFrame(rows).to_html(index=False,border=0,escape=True)
    quality=pd.DataFrame([dict(date=day,exchange=details['exchange'],snapshots=details['snapshots'],
        source_first=details['first_source_time'],source_last=details['last_source_time'],
        archive_statuses=', '.join(details['archive_statuses'])) for day,details in report['raw_inventory'].items()]).to_html(index=False,border=0,escape=True)
    points=[]
    for row in report['grid']:
        points.append({key:row[key] for key in ('date','session','clock','eligible','matched_across_days')} |
                      {key:float(row[key]) if row[key] is not None else None for key in SOURCE_METRICS})
    summaries=[dict(date=g['date'],session=g['session'],matched={key:float(v) if v is not None else None for key,v in g['matched_medians'].items()},
                    daily={key:float(v) if v is not None else None for key,v in g['daily_fresh_medians'].items()}) for g in report['summaries']]
    data=dict(points=points,summaries=summaries,dates=report['dates'])
    acb_stem='dnse_acb_analysis_'+'_'.join(report['dates'])+'.html'
    acb=("<p><a href='../../"+acb_stem+"'>Báo cáo A1/B5 ACB đã xác minh</a></p>") if symbol=='ACB' and (folder.parent/acb_stem).exists() else ''
    content=("<!doctype html><html lang='vi'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(symbol)} · Phân tích DNSE</title><script src='../assets/plotly.min.js'></script>"
        "<style>body{font:15px system-ui;line-height:1.65;background:#f3f6fa;color:#17334b;margin:0}main{max-width:1260px;margin:auto;padding:28px 20px}"
        "h1{font-size:32px}.note{padding:16px;background:#fff0c6;border-radius:10px}.table{overflow:auto;background:white;margin:18px 0}"
        "th,td{padding:8px 12px;border-bottom:1px solid #dde6ee;white-space:nowrap}th{background:#e5edf5}.chart{background:white;margin:18px 0;border-radius:10px}"
        "a{color:#12647c}.status{padding:12px;background:#e4edf5;border-radius:8px}</style></head><body><main>"
        f"<a href='../index.html'>← Tất cả mã</a><h1>{html.escape(symbol)} · Quan sát sổ lệnh DNSE</h1>"
        f"<p>{html.escape(', '.join(report['dates']))} · {html.escape(STATE_LABELS.get(report['state'],report['state']))} · {report['common_minutes_each_day']} phút chung mỗi ngày</p>"
        f"<p class='status'>A1/B5: {html.escape(FINANCE_LABELS.get(report['financial_status'],report['financial_status']))}<br>Phạm vi đơn vị: {html.escape(', '.join(report['observed_unit_statuses']) or 'Chưa có snapshot để đối chiếu')}</p>"
        f"<p class='note'>{html.escape(UNIT_NOTE)}</p>{acb}<h2>So sánh trên tập phút chung</h2><div class='table'>{table}</div>"
        "<p>Không có phút chung nghĩa là chưa có cơ sở so sánh theo phương pháp này, kể cả khi kho có bản ghi. "
        "Không coi một thông điệp không đổi trong nhiều phút là dữ liệu mới.</p>"
        f"<label><input id='common' type='checkbox' {'checked' if report['common_minutes_each_day'] else ''}> Chỉ vẽ các phút chung giữa các ngày</label>"
        "<p>Bỏ chọn sẽ xem các mẫu đủ tươi của từng ngày; khi đó các ngày có thể dùng tập phút khác nhau.</p>"
        "<div id='depth' class='chart'></div><div id='spread' class='chart'></div><div id='obi' class='chart'></div><div id='bars' class='chart'></div>"
        f"<h2>Độ phủ nguồn</h2><div class='table'>{quality}</div><h2>Phương pháp</h2><p>{html.escape(METHOD)}</p>"
        "<p>Dữ liệu tải sau phiên, tái dựng theo thời gian nguồn; không phải thông tin chương trình đã biết trong phiên. "
        "Thời điểm thực nhận, event ID và mọi lý do loại trừ được giữ trong tệp JSON gzip đi kèm. "
        "Hai ngày không đủ kết luận dài hạn. NEO chưa tích hợp; không có API giao dịch.</p>"
        f"<p><a href='{symbol}.json.gz'>Dữ liệu và truy vết JSON gzip</a></p></main>"
        f"<script>const data={encoded(data)};"
        "const labels={bid_depth_source:'Dư mua (đơn vị nguồn)',ask_depth_source:'Dư bán (đơn vị nguồn)',spread_bps:'Spread (bps)',obi:'OBI'};"
        "function draw(){const common=document.getElementById('common').checked;const ok=p=>p.eligible&&(!common||p.matched_across_days);"
        "const base={paper_bgcolor:'#ffffff',plot_bgcolor:'#ffffff',height:440,margin:{t:65,b:115,l:70,r:25},legend:{orientation:'h',y:-.25},xaxis:{title:{text:'Giờ nguồn'},type:'category',gridcolor:'#e5edf5'},yaxis:{gridcolor:'#e5edf5'}};"
        "for(const [id,keys,title] of [['depth',['bid_depth_source','ask_depth_source'],'Độ sâu quan sát · đơn vị khối lượng nguồn'],"
        "['spread',['spread_bps'],'Relative spread · bps'],['obi',['obi'],'OBI ba mức · tỷ lệ']]){let traces=[];"
        "for(const day of data.dates){for(const session of ['AM','PM']){const pts=data.points.filter(p=>p.date===day&&p.session===session);"
        "for(const key of keys){traces.push({x:pts.map(p=>p.clock),y:pts.map(p=>ok(p)?p[key]:null),mode:'lines',connectgaps:false,"
        "name:day+' '+session+' '+labels[key],type:'scatter'});}}}Plotly.newPlot(id,traces,{...base,title:{text:title}},{responsive:true});}"
        "const bars=['bid_depth_source','ask_depth_source'].map(key=>({type:'bar',name:labels[key],x:data.summaries.map(g=>g.date+' '+g.session),"
        "y:data.summaries.map(g=>(common?g.matched:g.daily)[key])}));"
        "Plotly.newPlot('bars',bars,{...base,title:{text:'Median độ sâu · đơn vị nguồn'},barmode:'group'},{responsive:true});}"
        "document.getElementById('common').addEventListener('change',draw);draw();</script></body></html>")
    (folder/'symbols'/(symbol+'.html')).write_text(content,encoding='utf-8')


def index_page(rows, folder, result):
    body=[]
    for row in rows:
        symbol=row['symbol']
        days=' · '.join(f"{day}: {details['snapshots']:,}" for day,details in row['raw_inventory'].items())
        exchanges='/'.join(sorted({d['exchange'] for d in row['raw_inventory'].values()}))
        body.append(f"<tr><td><a href='symbols/{symbol}.html'>{symbol}</a></td><td>{html.escape(exchanges)}</td>"
            f"<td>{html.escape(days)}</td><td>{row['common_minutes_each_day']}</td><td>{html.escape(STATE_LABELS.get(row['state'],row['state']))}</td>"
            f"<td>{html.escape(FINANCE_LABELS.get(row['financial_status'],row['financial_status']))}</td></tr>")
    code=("<!doctype html><html lang='vi'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>LiquidityLab · Các mã DNSE</title><style>body{font:15px system-ui;line-height:1.6;background:#f3f6fa;color:#17334b;margin:0}"
        "main{max-width:1400px;margin:auto;padding:28px 20px}.note{background:#fff1c8;padding:16px;border-radius:10px}"
        "input{padding:12px;width:min(520px,85%);border:1px solid #b8cbd9;border-radius:8px}table{background:white;border-collapse:collapse;width:100%}"
        "td,th{padding:9px;border-bottom:1px solid #e0e8ef;text-align:left;font-size:13px}th{background:#e5edf5}a{color:#12647c}.table{overflow:auto}</style><main>"
        f"<h1>Phân tích mô tả · {result['symbols_total']:,} mã trong kho DNSE</h1><p>{html.escape(', '.join(result['dates']))}</p>"
        f"<p class='note'>{html.escape(UNIT_NOTE)}</p><p>{html.escape(METHOD)}</p>"
        f"<p>{html.escape('; '.join(STATE_LABELS.get(k,k)+': '+str(v)+' mã' for k,v in result['symbol_states'].items()))}</p>"
        "<p>ACB là mẫu kiểm soát đã xác minh. Các mã còn lại được phân tích sổ lệnh theo phạm vi dữ liệu hiện có; không mở A1/B5 bằng giả định đơn vị.</p>"
        "<input id='search' placeholder='Tìm mã, sàn hoặc trạng thái…' aria-label='Tìm mã hoặc sàn'><p id='count'></p>"
        "<div class='table'><table><thead><tr><th>Mã</th><th>Sàn</th><th>Snapshot trong kho</th><th>Phút chung/ngày</th><th>Phân tích</th><th>A1/B5</th></tr></thead><tbody>"
        +''.join(body)+"</tbody></table></div><p><a href='summary.csv'>Bảng tổng hợp CSV</a> · <a href='manifest.json'>Manifest</a></p>"
        "<p>Không coi API trả rỗng là khối lượng bằng 0. Mã không còn trong danh mục một ngày được ghi riêng. "
        "Báo cáo dùng dữ liệu đã thu, không gọi mạng khi mở; cần giữ thư mục assets cùng thư mục symbols.</p></main>"
        "<script>const rows=[...document.querySelectorAll('tbody tr')];function search(){let n=0;const q=document.getElementById('search').value.toUpperCase();"
        "for(const r of rows){const show=r.innerText.toUpperCase().includes(q);r.hidden=!show;if(show)n++;}document.getElementById('count').innerText=n+' mã đang hiển thị';}"
        "document.getElementById('search').addEventListener('input',search);search();</script></html>")
    (folder/'index.html').write_text(code,encoding='utf-8')


def run(root=ROOT,dates=None,symbols=None,output=None,refresh_pages=False):
    root=Path(root).resolve()
    store=DNSEHistory(root/'data/processed/dnse/normalized.db')
    dates=sorted(set(dates or store.dates()[-2:]))
    if not dates or any(day not in store.dates() for day in dates):
        raise ValueError('SOURCE_DATES_NOT_AVAILABLE')
    folder=Path(output) if output else root/'report'/('dnse_market_analysis_'+'_'.join(dates))
    (folder/'symbols').mkdir(parents=True,exist_ok=True)
    (folder/'assets').mkdir(exist_ok=True)
    asset=folder/'assets/plotly.min.js'
    if not asset.exists():
        asset.write_text(get_plotlyjs(),encoding='utf-8')
    manifest_path=root/'data/processed/dnse/manifest.json'
    fingerprint=hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.exists() else 'FIXTURE_NO_MANIFEST'
    cache_key=dict(version=VERSION,input_manifest_sha256=fingerprint,dates=dates)
    rows=[]
    started=time.monotonic()
    last=0
    with closing(store.connect()) as db:
        universe=inventory(db,dates)
        selected=sorted(set(symbols or universe))
        if any(symbol not in universe for symbol in selected):
            raise ValueError('SYMBOL_NOT_IN_SELECTED_ARCHIVES')
        for index,symbol in enumerate(selected,1):
            if not re.fullmatch('[A-Z0-9]{1,12}',symbol):
                raise ValueError('UNSAFE_SYMBOL_FILENAME')
            cache=folder/'symbols'/(symbol+'.json.gz')
            page=folder/'symbols'/(symbol+'.html')
            if cache.exists() and page.exists():
                with gzip.open(cache,'rt',encoding='utf-8') as stream:
                    report=json.load(stream)
                if report.get('cache_key')!=cache_key:
                    raise ValueError('REPORT_CACHE_INPUT_CHANGED_USE_NEW_OUTPUT_DIRECTORY')
                if refresh_pages:
                    write_symbol(report,folder)
            else:
                report=analyse_symbol(db,universe[symbol],dates)
                report['cache_key']=cache_key
                write_symbol(report,folder)
            rows.append({key:report[key] for key in ('symbol','state','financial_status','common_minutes_each_day','observed_unit_statuses','raw_inventory','summaries')})
            if time.monotonic()-last>=20 or index==len(selected):
                progress=dict(state='RUNNING',symbols_completed=index,symbols_total=len(selected),current_symbol=symbol,
                              elapsed_seconds=round(time.monotonic()-started,1),**cache_key)
                (folder/'progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
                print(json.dumps(progress),flush=True)
                last=time.monotonic()
    result=dict(state='COMPLETED',symbols_total=len(rows),dates=dates,version=VERSION,
        symbol_states=dict(Counter(r['state'] for r in rows)),financial_states=dict(Counter(r['financial_status'] for r in rows)),
        input_manifest_sha256=fingerprint,rule_changes_applied=False,financial_simulation_performed=False,
        generated_at_local=datetime.now(LOCAL_TZ).isoformat(),methodology=METHOD,unit_note=UNIT_NOTE,
        index=str(folder/'index.html'))
    (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    flat=[]
    for row in rows:
        for group in row['summaries']:
            flat.append(dict(symbol=row['symbol'],state=row['state'],financial_status=row['financial_status'],
                **{key:group[key] for key in ('date','session','catalog_present','grid_points','eligible_points','matched_points')},
                **group['matched_medians']))
    pd.DataFrame(flat).to_csv(folder/'summary.csv',index=False,encoding='utf-8-sig')
    index_page(rows,folder,result)
    (folder/'progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dates',nargs='+')
    parser.add_argument('--symbols',nargs='+',help='Optional subset; default all symbols including API-empty ones')
    parser.add_argument('--output')
    parser.add_argument('--refresh-pages',action='store_true',help='Regenerate HTML from matching cached analysis')
    args=parser.parse_args()
    print(json.dumps(run(dates=args.dates,symbols=args.symbols,output=args.output,refresh_pages=args.refresh_pages),ensure_ascii=True),flush=True)


if __name__=='__main__':
    main()
