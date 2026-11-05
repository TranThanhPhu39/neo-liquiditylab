from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path

import plotly.graph_objects as go
import pytest

from src.app.depth_surface import depth_surface,figure_spec

DAY='2026-11-05'
AT=datetime.fromisoformat(DAY+'T14:20:59+07:00')


def record(clock='10:00',symbol='ACB'):
    return dict(symbol=symbol,source_timestamp=DAY+'T'+clock+':00+07:00',
        unit_verification_status='EMPIRICALLY_VERIFIED',
        **{f'{side}_price_{i}':str(20500+(-1 if side=='bid' else 1)*i*50)
           for side in ('bid','ask') for i in (1,2,3)},
        **{f'{side}_vol_{i}':i*100 for side in ('bid','ask') for i in (1,2,3)})


def test_geometry_uses_actual_price_time_quantity_and_preserves_source():
    rows=[record('10:00'),record('10:06')];before=deepcopy(rows)
    result=depth_surface(rows,'ACB',DAY,AT)
    assert rows==before and result['points']==2
    assert result['sides']['bid']['x'][0]==[20450,20400,20350]
    assert result['sides']['bid']['y'][0]==[36000]*3
    assert result['sides']['bid']['z'][0]==[100,200,300]
    assert result['sides']['bid']['cumulative'][0]==[100,300,600]
    spec=figure_spec(result,'both')
    go.Figure(data=spec['data'],layout=spec['layout'])  # Validate against Plotly's real schema.
    surfaces=[t for t in spec['data'] if t['type']=='surface']
    assert len(surfaces)==2 and all(t['connectgaps'] is False for t in surfaces)


def test_missing_is_not_zero_and_unknown_prefix_is_not_invented():
    row=record();row.update(bid_vol_1=0,bid_vol_2=None)
    grid=depth_surface([row],'ACB',DAY,AT)['sides']['bid']
    assert grid['z'][0]==[0,None,300]
    assert grid['cumulative'][0]==[0,None,None]
    row['bid_price_1']='ATC'
    assert depth_surface([row],'ACB',DAY,AT)['sides']['bid']['z'][0][0] is None


def test_no_future_other_day_or_other_symbol_and_timestamp_duplicates():
    old=record();old['source_timestamp']='2026-11-04T10:00:00+07:00'
    replacement=record();replacement['bid_vol_1']=999
    result=depth_surface([record(),old,record('14:21'),record('10:00','FPT'),replacement], 'ACB',DAY,AT)
    assert result['points']==1 and result['sides']['bid']['z'][0][0]==999
    assert all(t['type']=='scatter3d' for t in figure_spec(result)['data'])


def test_lunch_gap_has_null_geometry_not_interpolated_liquidity():
    result=depth_surface([record('11:29'),record('13:00')],'ACB',DAY,AT)
    assert result['gaps']==1 and result['points']==2
    for side in result['sides'].values():
        assert side['x'][1]==side['z'][1]==side['cumulative'][1]==[None]*3


def test_plot_sampling_is_bounded_and_retains_first_last():
    rows=[]
    start=datetime.fromisoformat(DAY+'T09:15:00+07:00')
    for i in range(1000):
        r=record();r['source_timestamp']=(start+timedelta(seconds=i)).isoformat();rows.append(r)
    result=depth_surface(rows,'ACB',DAY,AT,limit=180)
    assert result['points']==180 and result['available_points']==1000
    assert result['sides']['bid']['y'][0][0]==33300
    assert result['sides']['bid']['y'][-1][0]==34299


@pytest.mark.parametrize('value',[True,-1,'NaN','Infinity','1e999', '1.5'])
def test_invalid_quantities_never_become_chart_liquidity(value):
    r=record();r['ask_vol_1']=value
    assert depth_surface([r],'ACB',DAY,AT)['sides']['ask']['z'][0][0] is None


def test_unverified_labels_remain_unverified_and_empty_window_is_empty():
    r=record();r['unit_verification_status']='ASSUMED_SHARED_SCHEMA'
    assert not depth_surface([r],'ACB',DAY,AT)['units_verified']
    result=depth_surface([record('14:21')],'ACB',DAY,AT)
    assert result['points']==0 and figure_spec(result)['data']==[]


def test_vendored_3d_bundle_matches_pinned_manifest():
    folder=Path(__file__).resolve().parents[1]/'web/vendor'
    manifest=json.loads((folder/'plotly-manifest.json').read_text())
    content=(folder/'plotly-gl3d-3.7.0.min.js').read_bytes()
    assert len(content)==manifest['bytes'] and hashlib.sha256(content).hexdigest()==manifest['sha256']
    assert 'https://cdn.plot.ly/' in manifest['source']
