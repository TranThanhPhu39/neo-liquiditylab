from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from src.app.depth_surface import depth_surface

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node is required for browser model checks')


def test_native_app_including_unavailable_python_worker():
    result = subprocess.run([NODE, '--test', str(ROOT/'tests/js/native-dashboard.test.cjs')],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('cumulative', [False, True])
def test_native_geometry_matches_shared_python_geometry(cumulative):
    start = datetime.fromisoformat('2026-11-05T09:15:00+07:00')
    records = []
    for i in range(1000):
        r = dict(symbol='ACB', source_timestamp=(start+timedelta(seconds=i, microseconds=i)).isoformat(),
                 unit_verification_status='ASSUMED_SHARED_SCHEMA')
        for s in ('bid','ask'):
            for level in (1,2,3):
                r[f'{s}_price_{level}'] = str(21000+(-1 if s=='bid' else 1)*level*50)
                r[f'{s}_vol_{level}'] = None if i%20==0 and level==2 else i*level
        records.append(r)
    q = dict(day='2026-11-05', symbol='ACB', time='14:20', depth_mode='cumulative' if cumulative else 'volume')
    js = "const m=require('./web/market-view.js');let s='';process.stdin.on('data',x=>s+=x).on('end',()=>{const a=JSON.parse(s);process.stdout.write(JSON.stringify(m.surface(a.records,a.q)));});"
    result = subprocess.run([NODE, '-e', js], input=json.dumps(dict(records=records,q=q)),
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    native = json.loads(result.stdout)
    shared = depth_surface(records,'ACB',q['day'],datetime.fromisoformat(q['day']+'T14:20:59.999999+07:00'))
    assert (native['points'],native['available_points'],native['units_verified']) == (180,1000,False)
    for side in ('bid','ask'):
        assert native['grids'][side]['x'] == shared['sides'][side]['x']
        assert native['grids'][side]['z'] == shared['sides'][side]['cumulative' if cumulative else 'z']
        for actual,expected in zip(native['grids'][side]['y'],shared['sides'][side]['y']):
            assert actual == pytest.approx(expected, abs=1e-6)
