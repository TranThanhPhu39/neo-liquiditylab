"""Reproducible offline ACB report: equal-clock session comparisons, A1 and B5."""
import argparse
from datetime import datetime
import html
import json
from pathlib import Path

import pandas as pd
import plotly.express as px

from config.settings import ROOT
from src.data_pipeline.dnse_history import DNSEHistory
from src.finance.dnse_session_analysis import analyse_sessions, METRICS
from src.models.orderbook import LOCAL_TZ


NOTE = ('Tái dựng hồi tố theo thời gian nguồn từ dữ liệu tải sau phiên. Giữ nguyên thời điểm nhận thật; '
        'không phải backtest trên dữ liệu đã biết trong phiên. Lấy snapshot cuối đến hết mỗi phút, '
        'chỉ trong giờ liên tục HOSE 09:15–11:30 và 13:00–14:30, tuổi nguồn không quá 60 giây. '
        'Yêu cầu đủ ba mức hợp lệ mỗi phía và đơn vị đã xác minh. So sánh trên tập phút đủ điều kiện chung '
        'giữa các ngày, mỗi phút có trọng số như nhau. Điểm thiếu bị loại và được báo riêng; không nội suy qua giờ nghỉ.')
LIMITS = ('Hai ngày chỉ là ví dụ mô tả, không chứng minh xu hướng dài hạn hay xác suất khủng hoảng. '
          'Các phút liên tiếp có thể phụ thuộc nhau. Sổ lệnh ba mức không bao gồm lệnh mới/hủy, bổ sung thanh khoản '
          'hoặc ưu tiên hàng đợi. Chi phí so giá tốt nhất chỉ tính phần được bao phủ; phải đọc cùng coverage. '
          'Phí và thuế giả định bằng 0. Stress giảm khối lượng từng mức Bid, làm tròn xuống cổ phiếu nguyên, '
          'giữ nguyên giá và Ask; không phải dự báo. Không có khuyến nghị mua/bán hay gửi lệnh thật. NEO chưa tích hợp.')


def fmt(value, decimals=0, percentage=False):
    if value is None:
        return 'Chưa xác định'
    return f'{value:.1%}' if percentage else f'{value:,.{decimals}f}'


def summary_rows(report):
    rows = []
    for group in report['summaries']:
        m = group['medians']
        rows.append({'Ngày':group['date'], 'Phiên':group['session'],
            'Phút đủ điều kiện':f"{group['eligible_points']}/{group['grid_points']}",
            'Phút chung so sánh':group['matched_points'],
            'Median spread (bps)':fmt(m['spread_bps'],2),
            'Median Bid depth (CP)':fmt(m['bid_depth']), 'Median Ask depth (CP)':fmt(m['ask_depth']),
            'Median coverage bán 150.000 CP':fmt(m['sell_coverage_150000'],percentage=True),
            'Median cost bán (bps)':fmt(m['sell_cost_bps_150000'],2),
            'Tỷ lệ phút bao phủ đủ lệnh bán':fmt(group['full_sell_coverage_share_150000'],percentage=True),
            'Median coverage stress -50% Bid':fmt(m['stress50_coverage_150000'],percentage=True)})
    return rows


def stress_rows(report):
    return [{'Ngày':g['date'],'Phiên':g['session'],'Phút chung':g['matched_points'],
             'Median coverage stress -50%':fmt(g['medians']['stress50_coverage_150000'],percentage=True),
             'Coverage thấp nhất trong mẫu':fmt(g['stress50_min_coverage_150000'],percentage=True),
             'Tỷ lệ phút vẫn bao phủ đủ':fmt(g['stress50_full_coverage_share_150000'],percentage=True)}
            for g in report['summaries']]


def findings(report):
    last=report['dates'][-1]
    groups={g['session']:g for g in report['summaries'] if g['date']==last}
    lines=[]
    if all(groups.get(s,{}).get('matched_points') for s in ('AM','PM')):
        am,pm=groups['AM'],groups['PM']
        lines.append(f"Ngày {last}, median chi phí bán giả định 150.000 CP là {fmt(am['medians']['sell_cost_bps_150000'],2)} bps buổi sáng "
                     f"và {fmt(pm['medians']['sell_cost_bps_150000'],2)} bps buổi chiều, so với Bid tốt nhất và chỉ trên phần bao phủ.")
        lines.append(f"Với stress giảm 50% từng mức Bid chiều {last}, median coverage là {fmt(pm['medians']['stress50_coverage_150000'],percentage=True)} "
                     f"nhưng mức thấp nhất trong mẫu là {fmt(pm['stress50_min_coverage_150000'],percentage=True)}; "
                     f"tỷ lệ phút bao phủ đủ là {fmt(pm['stress50_full_coverage_share_150000'],percentage=True)}. Median cao không có nghĩa mọi phút đều đủ thanh khoản.")
    lines.append('Tổng độ sâu không quyết định riêng chi phí quét sổ: giá và cách phân bổ khối lượng giữa từng mức cũng ảnh hưởng kết quả. '
                 'Đây là mô tả hai ngày và các kịch bản giả định, không suy ra quan hệ nhân quả hoặc dự báo ngày tiếp theo.')
    return lines


def figures(report):
    frame = pd.DataFrame(report['grid'])
    for key in METRICS:
        frame[key] = pd.to_numeric(frame[key],errors='coerce')
        frame.loc[~frame['matched_across_days'],key] = None
    depth = frame.melt(id_vars=['date','session','clock'],value_vars=['bid_depth','ask_depth'],var_name='side',value_name='depth')
    depth['side']=depth['side'].map({'bid_depth':'Bid','ask_depth':'Ask'})
    figs = [px.line(depth,x='clock',y='depth',color='side',line_dash='session',facet_col='date',
                    title='Độ sâu ba mức tại các phút chung · CP'),
            px.line(frame,x='clock',y='spread_bps',color='session',facet_col='date',
                    title='Relative spread tại các phút chung · bps')]
    executions = pd.DataFrame(report['execution'])
    executions = executions[executions['matched_across_days']].copy()
    for key in ('coverage','cost_bps'):
        executions[key] = pd.to_numeric(executions[key],errors='coerce')
    curve = executions.groupby(['date','session','side','quantity'],as_index=False)[['coverage','cost_bps']].median()
    figs.extend([px.line(curve,x='quantity',y='coverage',color='date',facet_col='side',facet_row='session',markers=True,
                         title='A1 · Median coverage theo quy mô lệnh'),
                 px.line(curve,x='quantity',y='cost_bps',color='date',facet_col='side',facet_row='session',markers=True,
                         title='A1 · Median chi phí trên phần được bao phủ · bps')])
    shocks = pd.DataFrame(report['stress'])
    shocks = shocks[shocks['matched_across_days'] & (shocks['quantity']==150000)].copy()
    shocks['coverage'] = pd.to_numeric(shocks['coverage'])
    shocks['depth_reduction'] = pd.to_numeric(shocks['depth_reduction'])*100
    bars = shocks.groupby(['date','session','depth_reduction'])['coverage'].agg(['median','min']).reset_index()
    bars=bars.melt(id_vars=['date','session','depth_reduction'],value_vars=['median','min'],var_name='statistic',value_name='coverage')
    bars['statistic']=bars['statistic'].map({'median':'Median','min':'Thấp nhất trong mẫu'})
    figs.append(px.bar(bars,x='depth_reduction',y='coverage',color='date',facet_col='session',facet_row='statistic',barmode='group',
                       title='B5 · Coverage bán 150.000 CP khi giảm độ sâu Bid (%)'))
    for fig in figs:
        fig.update_layout(template='plotly_white',height=480,margin=dict(l=35,r=20,t=80,b=55),legend_title_text='')
        fig.update_traces(connectgaps=False,selector=dict(type='scatter'))
    figs[2].update_yaxes(tickformat='.0%',range=[0,1.05])
    figs[4].update_yaxes(tickformat='.0%',range=[0,1.05])
    figs[4].update_layout(height=650)
    return figs


def write_report(report, stem):
    stem = Path(stem)
    stem.parent.mkdir(parents=True,exist_ok=True)
    report = dict(report, generated_at_local=datetime.now(LOCAL_TZ).isoformat(), methodology=NOTE, limitations=LIMITS)
    report['findings']=findings(report)
    stem.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    for field in ('grid','execution','stress'):
        pd.DataFrame(report[field]).to_csv(stem.with_suffix('.'+field+'.csv'),index=False,encoding='utf-8-sig')
    rows = summary_rows(report)
    columns = list(rows[0])
    md = ['# ACB · Phân tích thanh khoản DNSE ngày '+', '.join(report['dates']), '', NOTE, '',
          '## Những điểm quan sát được','']
    md.extend('- '+line for line in report['findings'])
    md.extend(['','## So sánh trên các phút chung', '',
          '| '+' | '.join(columns)+' |','|'+'|'.join(['---']*len(columns))+'|']
    )
    md.extend('| '+' | '.join(str(row[c]) for c in columns)+' |' for row in rows)
    tails=stress_rows(report)
    tail_columns=list(tails[0])
    md.extend(['','## B5 · Median và các phút thanh khoản yếu','',
               'Lệnh bán giả định 150.000 CP, giảm 50% từng mức Bid, giữ nguyên giá. Tỷ lệ dưới đây chỉ mô tả tập phút chung.', '',
               '| '+' | '.join(tail_columns)+' |','|'+'|'.join(['---']*len(tail_columns))+'|'])
    md.extend('| '+' | '.join(str(row[c]) for c in tail_columns)+' |' for row in tails)
    md.extend(['','Median được tính riêng từng chỉ tiêu; không suy ra chi phí của lệnh lớn từ lệnh nhỏ. '
               'Tỷ lệ phút bao phủ đủ chỉ mô tả mẫu, không phải xác suất khớp lệnh tương lai.',
               '', '## Chất lượng mẫu', ''])
    for group in report['summaries']:
        md.append(f"- {group['date']} {group['session']}: {group['eligible_points']}/{group['grid_points']} phút đủ điều kiện, "
                  f"{group['matched_points']} phút chung; loại trừ: {json.dumps(group['exclusion_reasons'],ensure_ascii=False)}.")
    md.extend(['','## Mô phỏng và dữ liệu xuất','',
        'A1: lệnh mua và bán giả định 10.000, 50.000, 150.000, 500.000 CP; benchmark giá tốt nhất, phí/thuế 0. '
        'B5: bán các quy mô trên, giảm Bid 0%, 25%, 50%, 75%. Tiền và VWAP chỉ áp dụng cho phần được bao phủ. '
        'So sánh proceeds giữa các kịch bản không được coi là lỗ danh mục.', '',
        f"- [Báo cáo HTML tự chứa và biểu đồ tương tác]({stem.name}.html)",
        f"- [Dữ liệu và phương pháp JSON]({stem.name}.json)",
        f"- [Mọi mốc phút, gồm điểm bị loại]({stem.name}.grid.csv)",
        f"- [Chi tiết A1, số tiền Decimal]({stem.name}.execution.csv)",
        f"- [Chi tiết B5, số tiền Decimal]({stem.name}.stress.csv)",
        '', '## Giới hạn', '', LIMITS])
    stem.with_suffix('.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    figs = figures(report)
    quality = pd.DataFrame([dict(date=g['date'],session=g['session'],grid=g['grid_points'],eligible=g['eligible_points'],
                                 common=g['matched_points'],excluded=json.dumps(g['exclusion_reasons'],ensure_ascii=False))
                            for g in report['summaries']])
    stats = pd.DataFrame(rows).to_html(index=False,escape=True,border=0)
    tail_table=pd.DataFrame(tails).to_html(index=False,escape=True,border=0)
    observation_html='<ul>'+''.join('<li>'+html.escape(line)+'</li>' for line in report['findings'])+'</ul>'
    chart_html = ''.join(f.to_html(full_html=False,include_plotlyjs=(True if i==0 else False)) for i,f in enumerate(figs))
    common = sum(g['matched_points'] for g in report['summaries'] if g['date']==report['dates'][0])
    snapshots = sum(w['snapshots'] for w in report['windows'].values())
    title = 'ACB · Thanh khoản qua hai phiên trong ngày'
    content = ("<!doctype html><html lang='vi'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>body{{font:16px system-ui;line-height:1.65;margin:0;background:#f3f6fa;color:#183047}}"
        "main{max-width:1280px;margin:auto;padding:32px 20px}.tag{color:#42647d;font-size:13px;letter-spacing:.08em}"
        "h1{font-size:36px;line-height:1.2}h2{margin-top:38px}.note{background:#fff1cd;padding:18px;border-radius:10px}"
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:24px 0}"
        ".card{background:white;padding:18px;border-radius:12px}.card b{font-size:27px;display:block}.table{overflow:auto;background:white}"
        "td,th{padding:9px 12px;border-bottom:1px solid #e3e9ef;text-align:right;white-space:nowrap}th{background:#e8eff6}"
        ".chart{background:white;margin:18px 0;border-radius:12px;overflow:auto}a{color:#126b83}</style></head><body><main>"
        f"<div class='tag'>NEO LIQUIDITYLAB · DNSE · PHÂN TÍCH HỒI TỐ</div><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(', '.join(report['dates']))} · Dữ liệu thật đã chuẩn hóa · {html.escape(report['generated_at_local'])}</p>"
        f"<div class='cards'><div class='card'>Snapshot ACB trong kho<b>{snapshots:,}</b></div>"
        f"<div class='card'>Phút chung mỗi ngày<b>{common}</b></div><div class='card'>Ngày so sánh<b>{len(report['dates'])}</b></div>"
        f"<div class='card'>Tuổi nguồn tối đa<b>{report['max_age_seconds']} giây</b></div></div>"
        f"<p class='note'>{html.escape(NOTE)}</p><h2>Những điểm quan sát được</h2>{observation_html}"
        f"<h2>So sánh sáng và chiều</h2><div class='table'>{stats}</div>"
        "<p>Mỗi median tính riêng trên cùng tập phút. Chi phí chỉ áp dụng phần bao phủ; đọc cùng coverage. "
        f"Tỷ lệ phút bao phủ đủ không phải xác suất giao dịch tương lai.</p><h2>B5 · Những phút thanh khoản yếu</h2><div class='table'>{tail_table}</div><h2>Biểu đồ</h2>"
        f"<div class='chart'>{chart_html}</div><h2>Độ phủ và các điểm loại trừ</h2>"
        f"<div class='table'>{quality.to_html(index=False,escape=True,border=0)}</div>"
        "<p>AM: 135 mốc; PM: 90 mốc mỗi ngày. Không lấy bản hợp lệ cũ hơn để thay thế bản mới nhất đang lỗi. "
        "CSV mốc phút lưu event ID, thời gian nguồn, thời điểm thực nhận và lý do loại trừ.</p>"
        f"<h2>Giới hạn</h2><p class='note'>{html.escape(LIMITS)}</p>"
        "<p>A1: mua/bán 10.000, 50.000, 150.000, 500.000 CP. B5: giảm từng mức Bid 0%, 25%, 50%, 75%, giữ giá và Ask. "
        "Các đồ thị tiền chỉ là mô phỏng, không định giá phần chưa bao phủ.</p>"
        f"<p><a href='{html.escape(stem.name)}.md'>Bản Markdown</a> · <a href='{html.escape(stem.name)}.json'>Dữ liệu JSON</a></p>"
        "</main></body></html>")
    stem.with_suffix('.html').write_text(content,encoding='utf-8')
    return dict(html=str(stem.with_suffix('.html')),markdown=str(stem.with_suffix('.md')),
                common_minutes_per_day=common,grid_points=len(report['grid']),snapshots_in_store=snapshots)


def run(root=ROOT, dates=None, output_stem=None):
    root = Path(root).resolve()
    path = root/'data/processed/dnse/normalized.db'
    store = DNSEHistory(path)
    dates = dates or store.dates()[-2:]
    report = analyse_sessions(store,dates)
    manifest = root/'data/processed/dnse/manifest.json'
    if manifest.exists():
        report['input_manifest'] = json.loads(manifest.read_text(encoding='utf-8'))
    stem = Path(output_stem) if output_stem else root/'report'/('dnse_acb_analysis_'+'_'.join(dates))
    return write_report(report,stem)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dates',nargs='+',help='Source dates YYYY-MM-DD; default last two archived days')
    parser.add_argument('--output-stem')
    args = parser.parse_args()
    print(json.dumps(run(dates=args.dates,output_stem=args.output_stem),ensure_ascii=True))


if __name__=='__main__':
    main()
