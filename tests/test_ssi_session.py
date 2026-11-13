"""SSI board-session routing, clock ordering and detector eligibility."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.data_pipeline.ssi_live_normalization import select_session_flag,normalize_quote
from src.data_providers.ssi_stream import public_record,requests
from src.monitoring.spread import features

LOCAL=ZoneInfo('Asia/Ho_Chi_Minh')


def flag(value='LO',clock='10:10:00',topic='market.flag',connection=1):
    return dict(kind='MARKET',symbol='',topic=topic,connection=connection,
                payload=dict(b='HOSE',t='2026/11/13 '+clock,f=value),
                received_at='2026-11-13T'+clock+'+07:00')


def quote(clock='10:10:01'):
    return dict(kind='QUOTE',symbol='ACB',connection=1,received_at='2026-11-13T'+clock+'+07:00',
                payload=dict(s='ACB',t='2026/11/13 '+clock,
                    bids=[[str(20000-i*50),'1000'] for i in range(10)],
                    asks=[[str(20050+i*50),'1000'] for i in range(10)]))


CONTRACT=dict(status='EMPIRICALLY_VERIFIED',symbols=['ACB'],price_multiplier='1',
              volume_multiplier='1',quote_semantics='FULL_SNAPSHOT',hash='session-fixture')


def test_official_flag_topic_is_subscribed_and_keeps_only_public_hose_fields():
    assert sum(r['topics']==['market.flag'] for r in requests(['ACB']))==1
    f=dict(channel='DATA',topic='market.flag',data=dict(flag()['payload'],account='discarded'))
    r=public_record(f,['ACB'])
    assert r['kind']=='MARKET' and r['symbol']=='' and set(r['payload'])=={'b','t','f'}
    assert r['payload']['f']=='LO' and 'account' not in str(r)
    with pytest.raises(Exception,match='AUTH_MATERIAL'):
        public_record(f,['ACB'],credentials=('LO',))
    with pytest.raises(Exception,match='SCOPE'):
        public_record(dict(f,data=dict(f['data'],s='OTHER')),['ACB'])
    with pytest.raises(Exception,match='SESSION_SCHEMA'):
        public_record(dict(f,data={'b':'HOSE','f':'LO'}),['ACB'])


@pytest.mark.parametrize('board',['HNX','UPCOM','DER','DERIVATIVES'])
def test_broadcast_flags_for_other_boards_do_not_stop_or_enter_hose_collector(board):
    f=dict(channel='DATA',topic='market.flag',data=dict(flag()['payload'],b=board))
    assert public_record(f,['ACB']) is None


def test_index_c_cannot_overwrite_direct_lo_and_newer_direct_halt_still_wins():
    selected,_=select_session_flag({},flag())
    old=deepcopy(selected)
    selected,decision=select_session_flag(selected,flag('C','10:10:01','indexsummary.VNINDEX'))
    assert decision=='IGNORED_LOWER_PRIORITY' and selected==old
    r,_=normalize_quote(quote('10:10:02'),CONTRACT,'one',selected)
    assert features(r)['eligible'] and r['market_flag_source']=='market.flag'
    selected,_=select_session_flag(selected,flag('HALT','10:10:03','market.hose'))
    r,_=normalize_quote(quote('10:10:04'),CONTRACT,'halt',selected)
    assert not features(r)['eligible'] and r['market_flag']=='HALT'


def test_old_or_wrong_clock_flags_cannot_replace_newer_board_state():
    selected,_=select_session_flag({},flag('HALT','10:10:05'))
    unchanged,decision=select_session_flag(selected,flag('LO','10:10:00'))
    assert unchanged==selected and decision=='IGNORED_OLDER_FLAG'
    future=flag();future['received_at']='2026-11-13T09:00:00+07:00'
    assert select_session_flag(selected,future)==(selected,'IGNORED_FLAG_CLOCK')
    previous_day=flag();previous_day['payload']['t']='2026/11/12 10:10:00'
    assert select_session_flag(selected,previous_day)==(selected,'IGNORED_FLAG_CLOCK')


def test_conflicting_direct_flags_block_detector_until_newer_unambiguous_flag():
    selected,_=select_session_flag({},flag())
    selected,decision=select_session_flag(selected,flag('HALT',topic='market.hose'))
    assert decision=='CONFLICTING_FLAGS'
    r,_=normalize_quote(quote(),CONTRACT,'conflict',selected)
    assert r['market_flag_conflict'] and not features(r)['eligible']
    selected,_=select_session_flag(selected,flag('LO','10:10:02'))
    r,_=normalize_quote(quote('10:10:03'),CONTRACT,'resolved',selected)
    assert not r['market_flag_conflict'] and features(r)['eligible']


def test_lo_cannot_cross_lunch_connection_or_quote_source_time():
    selected,_=select_session_flag({},flag('LO','09:15:00'))
    for clock in ('11:30:00','13:00:01','14:30:00'):
        r,_=normalize_quote(quote(clock),CONTRACT,'boundary',selected)
        assert not features(r)['eligible']
    selected,_=select_session_flag(selected,flag('LO','13:00:00'))
    r,_=normalize_quote(quote('13:00:01'),CONTRACT,'pm',selected)
    assert features(r)['eligible'] and r['market_session']=='SSI_CONTINUOUS_PM'
    earlier,_=normalize_quote(quote('12:59:59'),CONTRACT,'no-lookahead',selected)
    assert not features(earlier)['eligible']
    reconnected=dict(quote('13:00:02'),connection=2)
    assert not features(normalize_quote(reconnected,CONTRACT,'reconnect',selected)[0])['eligible']


def test_c_is_retained_without_becoming_lo_and_units_remain_independent_gate():
    selected,_=select_session_flag({},flag('C'))
    r,_=normalize_quote(quote(),CONTRACT,'unknown-c',selected)
    assert r['market_flag']=='C' and r['market_session']=='SSI_SESSION_UNKNOWN'
    selected,_=select_session_flag({},flag())
    r,_=normalize_quote(quote(),dict(CONTRACT,status='UNVERIFIED',symbols=[]),'units',selected)
    assert r['market_session']=='SSI_CONTINUOUS_AM' and features(r)['reason']=='UNVERIFIED_UNITS'
