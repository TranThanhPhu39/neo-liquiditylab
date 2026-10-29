"""Report empty DNSE responses separately from errors, pending work and catalogue changes."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from config.settings import ROOT
from scripts.collect_dnse_history import VN,utcnow
from scripts.scheduled_collection import write_json


def make_report(day,*,session='morning',root=ROOT):
    if session not in ('day','morning'):
        raise ValueError('INVALID_COLLECTION_SESSION')
    base=root/'data/history/dnse'/day
    folder=base/session if session!='day' else base
    db=sqlite3.connect((folder/'history.db').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        settings=json.loads(db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])
        rows=[dict(row) for row in db.execute('''SELECT symbol,exchange,status,page_count,quote_count,
            first_time,last_time,error FROM symbols ORDER BY exchange,symbol''')]
        receipts={row['symbol']:dict(row) for row in db.execute('''SELECT symbol,received_at,sha256
            FROM pages WHERE quote_count=0 AND page_number=1''')}
    finally:
        db.close()
    old_rows={}
    previous_day=None
    history_root=root/'data/history/dnse'
    for old_folder in sorted((p for p in history_root.iterdir() if p.is_dir() and p.name<day),reverse=True):
        old_path=old_folder/'history.db'
        if old_path.exists():
            previous_day=old_folder.name
            previous=sqlite3.connect(old_path.as_uri()+'?mode=ro',uri=True)
            previous.row_factory=sqlite3.Row
            try:
                old_rows={row['symbol']:dict(row) for row in previous.execute(
                    'SELECT symbol,exchange,status,quote_count FROM symbols')}
            finally:
                previous.close()
            break
    current={row['symbol']:row for row in rows}
    missing=[]
    for row in rows:
        if row['status']=='NO_DATA_RETURNED':
            receipt=receipts.get(row['symbol'])
            if not receipt or row['quote_count']!=0:
                raise ValueError('EMPTY_RESPONSE_EVIDENCE_MISSING')
            missing.append({**row,'received_at_utc':receipt['received_at'],'response_sha256':receipt['sha256'],
                'previous_day_status':old_rows.get(row['symbol'],{}).get('status')})
    errors=[row for row in rows if row['status']=='ERROR']
    removed=[old_rows[symbol] for symbol in sorted(set(old_rows)-set(current))]
    empty_after_previous_records=[row['symbol'] for row in missing if old_rows.get(row['symbol'],{}).get('quote_count',0)>0]
    records_after_previous_empty=[row['symbol'] for row in rows if row['quote_count']>0
        and old_rows.get(row['symbol'],{}).get('status')=='NO_DATA_RETURNED']
    by_exchange={exchange:[row['symbol'] for row in missing if row['exchange']==exchange]
                 for exchange in ('HOSE','HNX','UPCOM')}
    manual_path=root/'data/control'/f"dnse_manual_checks_{day}_{session}.json"
    manual_zero=[]
    if manual_path.exists():
        evidence=json.loads(manual_path.read_text(encoding='utf-8'))
        if evidence.get('date')!=day or evidence.get('session')!=session:
            raise ValueError('MANUAL_VERIFICATION_SCOPE_MISMATCH')
        empty_symbols={row['symbol'] for row in missing}
        seen=set()
        for check in evidence.get('checks',[]):
            symbol=check.get('symbol')
            if symbol in empty_symbols and symbol not in seen and check.get('evidence_type')=='USER_REPORTED_ZERO_ACTIVITY_AND_DISPLAYED_DEPTH' and all(
                    check.get(field)==0 for field in ('matched_volume','displayed_bid_volume','displayed_ask_volume')):
                manual_zero.append(check)
                seen.add(symbol)
    manually_checked={row['symbol'] for row in manual_zero}
    remaining_by_exchange={exchange:[symbol for symbol in symbols if symbol not in manually_checked]
                           for exchange,symbols in by_exchange.items()}
    counts=Counter(row['status'] for row in rows)
    result={'provider':'dnse','date':day,'session':session,'generated_at_utc':utcnow(),
        'query_start_local':datetime.fromtimestamp(settings['from'],VN).isoformat(),
        'query_end_local':datetime.fromtimestamp(settings['to'],VN).isoformat(),
        'board':'G1','symbols_total':len(rows),'symbols_with_records':sum(row['quote_count']>0 for row in rows),
        'symbols_pending_first_page':sum(row['status']=='PENDING' for row in rows),
        'symbols_with_more_pages':counts['PARTIAL'],'empty_count':len(missing),'error_count':len(errors),
        'all_symbols_fully_paginated':all(row['status'] in ('COMPLETE','NO_DATA_RETURNED') for row in rows),
        'first_pass_finished':not any(row['status']=='PENDING' for row in rows),
        'empty_by_exchange':by_exchange,'empty_responses':missing,'errors':errors,
        'previous_comparison_day':previous_day,'not_in_current_dnse_universe':removed,
        'morning_empty_after_previous_day_records':empty_after_previous_records,
        'records_after_previous_day_empty':records_after_previous_empty,
        'manual_zero_display_checks':manual_zero,'manual_zero_display_count':len(manual_zero),
        'empty_responses_pending_manual_review':len(missing)-len(manual_zero),
        'pending_manual_review_by_exchange':remaining_by_exchange,
        'priority_pending_manual_review':[symbol for symbol in empty_after_previous_records if symbol not in manually_checked],
        'note':'Empty means API returned no quote records in this window. It does not mean zero order-book volume or prove no trading. Previous-day comparison may cover a different session.'}
    stem=root/'report'/f"dnse_missing_{day}{'_'+session if session!='day' else ''}"
    write_json(stem.with_suffix('.json'),result)
    title=f'DNSE — mã không nhận được dữ liệu, {"phiên sáng" if session=="morning" else "cả ngày"} {day}'
    lines=[f'# {title}','',f'Cửa sổ: **{result["query_start_local"]} đến {result["query_end_local"]}**, bảng G1.',
        '',f'- Danh mục hiện tại: **{len(rows)} mã**; có bản ghi: **{result["symbols_with_records"]}**.',
        f'- API trả rỗng: **{len(missing)} mã**; lỗi truy cập: **{len(errors)} mã**.',
        f'- Chưa có trang đầu: **{result["symbols_pending_first_page"]} mã**; còn trang tiếp: **{counts["PARTIAL"]} mã**.',
        f'- Đã tải hết trang toàn danh mục: **{"Có" if result["all_symbols_fully_paginated"] else "Chưa"}**.',
        f'- Đã đối chiếu thủ công bảng hiển thị 0: **{len(manual_zero)} mã**; còn cần kiểm tra: **{len(missing)-len(manual_zero)} mã**.',
        '', '**“API trả rỗng” là phản hồi hợp lệ với `quotes=[]` và không còn token. Không gán khối lượng bằng 0 hoặc tự kết luận mã không giao dịch.**','']
    if manual_zero:
        lines.extend(['## Đã đối chiếu: bảng giá hiển thị 0','',
            'Theo người dùng kiểm tra trên các nguồn dưới đây. Đây là bằng chứng bảng hiển thị, chưa phải lịch sử sổ lệnh đã được API xác nhận. Không tạo thêm snapshot hoặc sửa raw.','',
            '| Mã | Nguồn người dùng đối chiếu | Khớp lệnh | Dư mua | Dư bán |',
            '| --- | --- | ---: | ---: | ---: |'])
        lines.extend(f'| {check["symbol"]} | {", ".join(check.get("sources",[]))} | 0 | 0 | 0 |' for check in manual_zero)
        lines.append('')
    for exchange,symbols in by_exchange.items():
        lines.extend([f'## {exchange}: {len(symbols)} mã','',', '.join(symbols) if symbols else 'Chưa ghi nhận mã trả rỗng.',''])
    lines+=['## Mã không còn trong danh mục DNSE hiện tại','',
            ', '.join(row['symbol'] for row in removed) if removed else 'Không ghi nhận thay đổi so với bộ trước.',
            '',f'Đối chiếu với danh mục/bộ dữ liệu ngày {previous_day}. Không tự kết luận các mã này đã hủy niêm yết.','',
            '## Ưu tiên kiểm tra chéo','',
            'Chưa đối chiếu, sáng nay API trả rỗng nhưng bộ ngày trước có bản ghi: '+(', '.join(result['priority_pending_manual_review']) or 'Không còn mã cần đối chiếu trong nhóm này.')+'\n',
            'Ngày trước API trả rỗng nhưng sáng nay đã có bản ghi: '+(', '.join(records_after_previous_empty) or 'Chưa ghi nhận.')+'\n',
            'So sánh này có thể khác cửa sổ sáng/cả ngày; không tự coi thay đổi là lỗi nguồn.','',
            '## Lỗi truy cập riêng','']
    lines.extend([f'- {row["symbol"]} ({row["exchange"]}): {row["error"]}' for row in errors] or ['Không ghi nhận.'])
    stem.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    text=[title,f'Khoảng truy vấn: {result["query_start_local"]} -> {result["query_end_local"]}',
          f'API trả rỗng tổng cộng: {len(missing)} mã. Đã đối chiếu bảng hiển thị 0: {len(manual_zero)} mã.',
          'Đã đối chiếu theo người dùng: '+', '.join(sorted(manually_checked)),
          f'DANH SÁCH CÒN CẦN KIỂM TRA: {len(missing)-len(manual_zero)} mã.','']
    text.extend(f'{exchange} ({len(symbols)}): '+', '.join(symbols) for exchange,symbols in remaining_by_exchange.items())
    text.extend(['','Không còn trong danh mục DNSE: '+', '.join(row['symbol'] for row in removed)])
    stem.with_suffix('.txt').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--session',choices=['day','morning'],default='morning')
    args=parser.parse_args()
    result=make_report(args.date,session=args.session)
    print(json.dumps({key:result[key] for key in ('date','session','symbols_total','symbols_with_records',
        'symbols_pending_first_page','symbols_with_more_pages','empty_count','error_count','first_pass_finished',
        'all_symbols_fully_paginated','empty_by_exchange','not_in_current_dnse_universe',
        'morning_empty_after_previous_day_records','records_after_previous_day_empty',
        'manual_zero_display_count','empty_responses_pending_manual_review','priority_pending_manual_review')},ensure_ascii=True))


if __name__=='__main__':
    main()
