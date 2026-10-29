"""Prepare private comparison cases, capture one read-only quote, or compare an image transcription."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from config.settings import ROOT
from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.dnse_normalization import canonical_json, source_time
from src.data_pipeline.dnse_unit_review import candidates, historical_case, observation_template, compare_observation
from src.data_providers.dnse_readonly import DNSEReadOnly
from src.models.orderbook import LOCAL_TZ
import hashlib


def save_case(case, folder, name):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(name+'.case.json')
    observation=folder/(name+'.observation.json')
    if path.exists() or observation.exists():
        raise ValueError('REVIEW_CASE_ALREADY_EXISTS_USE_NEW_OUTPUT_DIRECTORY')
    path.write_text(json.dumps(case,ensure_ascii=False,indent=2),encoding='utf-8')
    observation.write_text(json.dumps(observation_template(case),ensure_ascii=False,indent=2),encoding='utf-8')
    return path,observation


def prepare(root=ROOT,day=None,output=None):
    root=Path(root).resolve()
    store=DNSEHistory(root/'data/processed/dnse/normalized.db')
    day=day or store.dates()[-1]
    selected=candidates(store,day)
    folder=Path(output) if output else root/'data/control'/('dnse_unit_review_'+day)
    at=datetime.fromisoformat(day+'T14:20:59.999999+07:00')
    rows=[]
    for item in [dict(symbol='ACB',exchange='HOSE',control=True)]+selected:
        case=historical_case(store,root,item['symbol'],day,at)
        path,observation=save_case(case,folder,item['symbol'])
        rows.append(dict(item,source_time_local=case['source_time_local'],
            source_age_at_cutoff_seconds=case['source_age_at_cutoff_seconds'],
            unit_verification_status=case['unit_verification_status'],case=str(path),observation=str(observation)))
    result=dict(state='PREPARED_NOT_VERIFIED',date=day,selection='Most stored snapshots per exchange; not an investment ranking',
                quotas=dict(HOSE=4,HNX=3,UPCOM=3),control_symbol='ACB',candidates=rows,
                rule_changes_applied=False,finance_scope_unchanged=True)
    report=root/'report'/('dnse_unit_review_plan_'+day)
    report.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Kế hoạch đối chiếu đơn vị DNSE · '+day,'',
        'Đã chuẩn bị 10 mã ngoài ACB: 4 HOSE, 3 HNX, 3 UPCOM; ACB là mẫu kiểm soát. '
        'Chọn theo số snapshot lưu được trong ngày, không phải khuyến nghị cổ phiếu. '
        'Các gói là mốc lịch sử đến hết phút 14:20; chỉ đối chiếu khi có ảnh đúng thời điểm nguồn. '
        'Không so ảnh phiên sau với gói lịch sử này.','',
        '| Mã | Sàn | Snapshot trong ngày | Thời gian nguồn của mẫu | Trạng thái đơn vị |',
        '|---|---|---:|---|---|']
    lines.extend(f"| {r['symbol']} | {r['exchange']} | {r.get('snapshots','Mẫu kiểm soát')} | {r['source_time_local']} | {r['unit_verification_status']} |" for r in rows)
    lines.extend(['','Mỗi gói chứa quote raw gốc, event ID, đường dẫn trang/ordinal, thời gian nhận thật và mẫu khai báo quan sát độc lập. '
        'Các trường giá/khối lượng quan sát đều để trống để tránh lấy số dự kiến làm bằng chứng.', '',
        'Nếu chưa có ảnh đúng thời điểm, trong phiên tiếp theo dùng `python -m scripts.dnse_unit_review capture --symbol MA` '
        'để lưu raw latest của từng mã gần thời điểm chụp bảng giá. Tệp capture vẫn ghi rõ độ trễ nguồn và chưa xác minh.', '',
        'Sau khi nhập thời gian, đơn vị và sáu cặp giá/khối lượng từ ảnh vào observation JSON, dùng lệnh compare trong '
        '[hướng dẫn](../docs/dnse_session_analysis.md). Khớp số chỉ tạo kết quả cần rà soát; không sửa hồ sơ đơn vị, không mở A1/B5.', '',
        'Các gói riêng ở `data/control/dnse_unit_review_'+day+'`; không đưa dữ liệu này lên repo public.'])
    report.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result


def capture(symbol,*,root=ROOT,client=None):
    if not re.fullmatch(r'[A-Z0-9]{1,12}',symbol):
        raise ValueError('INVALID_MARKET_SYMBOL')
    observation=(client or DNSEReadOnly()).get(f'/price/{symbol}/quotes/latest',{'boardId':'G1'})
    found=[]
    def walk(node):
        if isinstance(node,dict):
            if node.get('symbol')==symbol and node.get('boardId')=='G1' and 'time' in node and ('bid' in node or 'offer' in node):
                found.append(node)
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node,list):
            for value in node:
                walk(value)
    walk(observation['payload'])
    if len(found)!=1:
        raise ValueError('ONE_G1_QUOTE_REQUIRED')
    quote=found[0]
    received=datetime.fromisoformat(observation['received_at_utc']).astimezone(LOCAL_TZ)
    case=dict(schema='dnse-unit-review-v1',case_kind='READ_ONLY_LATEST_CAPTURE',symbol=symbol,
        raw_quote=quote,source_time_local=source_time(quote['time']).isoformat(),received_at_local=received.isoformat(),
        source_age_at_cutoff_seconds=(received-source_time(quote['time'])).total_seconds(),
        event_id='dnse:'+hashlib.sha256(canonical_json(quote).encode()).hexdigest(),
        price_multiplier_to_vnd='1000',quantity_multiplier_to_shares='10',
        unit_verification_status='NOT_REVIEWED_CAPTURE',rule_changes_applied=False)
    folder=Path(root)/'data/control/dnse_unit_review_captures'
    name=symbol+'_'+received.strftime('%Y%m%dT%H%M%S%f')
    path,template=save_case(case,folder,name)
    return dict(state='CAPTURED_NOT_VERIFIED',case=str(path),observation=str(template),
                source_time_local=case['source_time_local'],source_age_seconds=case['source_age_at_cutoff_seconds'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prep=commands.add_parser('prepare')
    prep.add_argument('--date')
    prep.add_argument('--output')
    latest=commands.add_parser('capture')
    latest.add_argument('--symbol',required=True)
    comparison=commands.add_parser('compare')
    comparison.add_argument('--case',required=True,type=Path)
    comparison.add_argument('--observation',required=True,type=Path)
    comparison.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.command=='prepare':
        result=prepare(day=args.date,output=args.output)
        print(json.dumps(dict(state=result['state'],symbols=[r['symbol'] for r in result['candidates']],rule_changes_applied=False)))
    elif args.command=='capture':
        print(json.dumps(capture(args.symbol)))
    else:
        case=json.loads(args.case.read_text(encoding='utf-8-sig'))
        observed=json.loads(args.observation.read_text(encoding='utf-8-sig'))
        result=compare_observation(case,observed,base=args.observation.parent)
        result['case_file_sha256']=hashlib.sha256(args.case.read_bytes()).hexdigest()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(status=result['status'],rule_changes_applied=False,finance_scope_unchanged=True)))


if __name__=='__main__':
    main()
