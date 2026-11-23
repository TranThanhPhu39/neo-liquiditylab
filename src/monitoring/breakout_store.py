"""Small read-only adapter over existing trade and replay Parquet packages."""
from datetime import date, timedelta
import json

from src.data_pipeline.parquet_history import confined
from src.monitoring.breakout import analyze, positive, prepare_trades
from src.monitoring.spread import stamp


def replay_frames(server, entry):
    if entry.get('market_observation'):
        for r in server.market.records(entry):
            yield dict(id=r['event_id'], time=r['source_timestamp'], session=r['market_session'],
                       units=r['unit_verification_status'], quality=r['data_quality_status'],
                       bids=[[r[f'bid_price_{i}'], r[f'bid_vol_{i}']] for i in (1, 2, 3)],
                       asks=[[r[f'ask_price_{i}'], r[f'ask_vol_{i}']] for i in (1, 2, 3)])
    elif entry.get('compact_folder'):
        import pyarrow.parquet as pq
        server.dataset(entry['dataset_id'])  # Apply established confined-path validation.
        source = pq.ParquetFile(confined(server.root, entry['compact_folder']) / 'frames.parquet')
        if source.metadata.num_rows > 100000:
            raise ValueError('RESEARCH_BOOK_LIMIT')
        for batch in source.iter_batches(batch_size=128, columns=['frame_json']):
            for row in batch.to_pylist():
                yield json.loads(row['frame_json'])
    else:
        value = json.loads(server.dataset(entry['dataset_id']).read_bytes())
        yield from value['frames']


def research(server, dataset_id, cutoff, reference_price=None, reference_day=None):
    entries = server.index()
    entry = next((e for e in entries if e['dataset_id'] == dataset_id), None)
    if entry is None:
        raise ValueError('UNKNOWN_DATASET')
    day, symbol = entry['day'], entry['symbol']
    current = date.fromisoformat(day)
    previous = current - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    ref_day = reference_day or previous.isoformat()
    if date.fromisoformat(ref_day) >= current:
        raise ValueError('RESEARCH_REFERENCE_MUST_BE_PAST')
    reference = dict(day=ref_day, price=None, kind='STORED_TRADE_HIGH', official=False,
                     notice='Đỉnh của giao dịch đã lưu, chưa chứng nhận đỉnh toàn phiên. Ngày mặc định là ngày làm việc trước; chưa xác nhận lịch nghỉ của sở.')
    if reference_price is not None:
        p = positive(reference_price)
        if p is None or p > 1000000000:
            raise ValueError('RESEARCH_REFERENCE_PRICE_INVALID')
        reference.update(price=str(p), kind='USER_SUPPLIED_PREVIOUS_HIGH',
                         notice='Đỉnh phiên trước do người xem nhập, chưa được hệ thống xác minh với OHLC chính thức.')
    else:
        prior = next((e for e in entries if e['day'] == ref_day and e['symbol'] == symbol
                      and e.get('exchange', 'HOSE') == entry.get('exchange', 'HOSE') and not e.get('archived')), None)
        if prior:
            pack = server.trades(prior['dataset_id'])
            allowed = {'EMPIRICALLY_VERIFIED_FOR_ACB_TRADES', 'EMPIRICALLY_VERIFIED', 'OFFICIALLY_VERIFIED',
                       'CUMULATIVE_SCALE_AND_MATCH_INCREMENT_RECONCILED'}
            if pack.get('symbol') != symbol or pack.get('day') != ref_day:
                raise ValueError('RESEARCH_REFERENCE_SCOPE_MISMATCH')
            if pack.get('price_unit') == 'VND' and pack.get('volume_unit') == 'shares' and pack.get('unit_status') in allowed:
                rows, invalid = prepare_trades(pack, ref_day, stamp(day + 'T00:00:00+07:00'))
                if rows and not invalid:
                    reference.update(price=str(max(r['price'] for r in rows)), observations=len(rows),
                                     trade_dataset_id=pack.get('trade_dataset_id'),
                                     volume_reconciliation=pack.get('quality', {}).get('volume_reconciliation', 'UNKNOWN'))
                del rows
            del pack
        if reference['price'] is None:
            reference['notice'] = 'Chưa có giao dịch hợp lệ của ngày tham chiếu. Chọn ngày đúng phiên trước hoặc nhập đỉnh đã kiểm tra; không thay bằng giá dư bán/mua.'
    pack = server.trades(dataset_id)
    # No trade data must remain unavailable, never reconstructed from quotes.
    result = analyze(pack=pack, frames=replay_frames(server, entry), reference=reference,
                     day=day, symbol=symbol, exchange=entry.get('exchange', 'HOSE'), cutoff=cutoff)
    result.update(dataset_id=dataset_id, reference_days=sorted({e['day'] for e in entries if e['day'] < day
                  and e['symbol'] == symbol and not e.get('archived')}, reverse=True))
    return result
