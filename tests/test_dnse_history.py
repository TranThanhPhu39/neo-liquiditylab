"""Exercise historical reconstruction and UI without API keys or user archives."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import pytest
from streamlit.testing.v1 import AppTest

from test_dnse_normalization import prepare_fixture, quote
from scripts.normalize_dnse_history import run as normalize, connect_output
from src.data_pipeline.dnse_history import DNSEHistory
from src.data_pipeline.dnse_normalization import snapshot_from_record
from src.data_pipeline.replay import latest_at
from src.data_providers.dnse_readonly import DNSEReadOnly
from src.finance.historical_analytics import historical_frame
from src.finance.liquidity_metrics import liquidity_metrics


@pytest.fixture
def history(tmp_path, quote, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline history must not call an API')
    monkeypatch.setattr(DNSEReadOnly,'get',forbidden)
    prepare_fixture(tmp_path,quote)
    normalize(root=tmp_path)
    return DNSEHistory(tmp_path/'data/processed/dnse/normalized.db')


def test_reconstruction_preserves_receipt_and_never_uses_future_or_previous_day(history):
    at=datetime.fromisoformat('2026-10-28T13:36:43.792+07:00')
    before=hashlib.sha256(history.path.read_bytes()).hexdigest()
    assert history.dates()==['2026-10-28']
    record=history.reconstruct_at(['ACB'],'2026-10-28',at)['ACB']
    book=snapshot_from_record(record)
    assert book.bids[0].volume==113300
    assert book.collected_at_utc > at
    assert latest_at([book],at)=={}  # Arrival-time replay is still strict.
    assert history.reconstruct_at(['ACB'],'2026-10-28',at.replace(second=42))=={}
    assert history.reconstruct_at(['ACB'],'2026-10-29',at.replace(day=29))=={}
    with pytest.raises(ValueError):
        history.reconstruct_at(['ACB'],'2026-10-28',at.replace(tzinfo=None))
    assert hashlib.sha256(history.path.read_bytes()).hexdigest()==before
    with history.connect() as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM snapshots')


def test_empty_response_remains_empty_and_unverified_metrics_stay_visible(history):
    assert history.catalog('2026-10-28')['EMG']['raw_quote_count']==0
    assert history.window('EMG','2026-10-28')['snapshots']==0
    at=datetime.fromisoformat('2026-10-28T14:00:00+07:00')
    assert history.reconstruct_at(['EMG'],'2026-10-28',at)=={}
    record=history.reconstruct_at(['FPT'],'2026-10-28',at)['FPT']
    frame=historical_frame([snapshot_from_record(record)])
    assert frame.iloc[0]['bid_depth']==301100
    assert frame.iloc[0]['coverage'] is None
    record.update(bid_price_1=None,bid_price_2=None,bid_price_3=None)
    metrics=liquidity_metrics(snapshot_from_record(record))
    assert metrics['bid_depth'] is None and metrics['obi'] is None


def test_chart_sampling_is_bounded_and_respects_cutoff(history):
    # Insert many events into this synthetic DB; no raw user archive is involved.
    db=connect_output(history.path)
    template=dict(db.execute("SELECT * FROM snapshots WHERE symbol='ACB'").fetchone())
    record=json.loads(zlib.decompress(template['payload_zlib']))
    columns=','.join(key for key in template if key!='id')
    keys=[key for key in template if key!='id']
    with db:
        for minute in range(120):
            at=datetime.fromisoformat(f'2026-10-28T0{2+minute//60}:{minute%60:02d}:00+00:00')
            payload={**record,'source_timestamp':at.isoformat()}
            row={**template,'event_key':f'sample-{minute}','source_time':at.isoformat(),
                 'payload_zlib':zlib.compress(json.dumps(payload).encode())}
            db.execute(f"INSERT INTO snapshots({columns}) VALUES({','.join('?' for _ in keys)})",[row[key] for key in keys])
    db.close()
    cutoff=datetime.fromisoformat('2026-10-28T10:00:00+07:00')
    samples=history.sampled_records('ACB','2026-10-28',cutoff,limit=24)
    assert 1<len(samples)<=24
    assert all(datetime.fromisoformat(r['source_timestamp'])<=cutoff for r in samples)
    assert datetime.fromisoformat(samples[-1]['source_timestamp'])==cutoff


def dashboard(history, monkeypatch):
    monkeypatch.setenv('DATA_SOURCE','dnse')
    monkeypatch.setenv('DNSE_DATABASE_PATH',str(history.path))
    return AppTest.from_file(str(Path(__file__).resolve().parents[1]/'src/app/main.py'),default_timeout=30).run()


def test_dnse_three_screens_and_unverified_guard(history,monkeypatch):
    at=dashboard(history,monkeypatch)
    assert not at.exception and not at.error
    assert at.metric[0].value=='301,100'
    at.sidebar.radio[0].set_value('Replay lịch sử').run()
    assert not at.exception
    at.sidebar.radio[1].set_value('Mô phỏng thực hiện lệnh').run()
    at.checkbox[0].check().run()
    assert not at.exception and at.metric[0].value=='100.0%'
    assert at.metric[2].value=='2,570,000'
    at.sidebar.selectbox(key='dnse_symbol').set_value('FPT').run()
    assert not at.exception and not at.metric
    assert any('đang khóa' in i.value for i in at.info)
    at.sidebar.radio[1].set_value('Stress danh mục').run()
    at.checkbox[0].check().run()
    assert not at.exception and at.metric[0].value=='100.0%'
    at.text_area[0].set_value('symbol,quantity\nACB,150000\nFPT,100').run()
    assert not at.exception and at.json
    assert any('chưa được xác minh' in json.loads(item.value).get('FPT',{}).get('reason','') for item in at.json)


def test_dnse_empty_symbol_and_replay_before_first_event(history,monkeypatch):
    at=dashboard(history,monkeypatch)
    at.sidebar.selectbox(key='dnse_symbol').set_value('EMG').run()
    assert not at.exception and not at.metric
    assert any('chưa thể suy ra' in i.value for i in at.info)
    at.sidebar.selectbox(key='dnse_symbol').set_value('ACB').run()
    at.sidebar.radio[0].set_value('Replay lịch sử').run()
    at.sidebar.time_input[0].set_value(datetime.strptime('09:00','%H:%M').time()).run()
    assert not at.exception and not at.metric
    assert any('không dùng snapshot tương lai' in i.value for i in at.info)
