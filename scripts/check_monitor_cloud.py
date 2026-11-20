"""Bounded HTTP smoke checks, with credentials read only from environment."""
import base64
import gzip
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen


def check(origin,password,*,public_read=False):
    def get(path,authenticated=False):
        headers={}
        if authenticated:headers['Authorization']='Basic '+base64.b64encode(('liquiditylab:'+password).encode()).decode()
        try:
            with urlopen(Request(origin+path,headers=headers),timeout=10) as r:return r.status,r.read()
        except HTTPError as e:return e.code,b''
    for i in range(30):
        try:
            if get('/healthz')[0]==200:break
        except OSError:pass
        time.sleep(1)
    else:raise RuntimeError('CLOUD_HEALTHCHECK_TIMEOUT')
    code,body=get('/')
    assert code==200 and b'LiquidityLab' in body and b'/dashboard' in body
    code,css=get('/intro.css')
    assert code==200 and b'--paper' in css
    assert get('/dashboard')[0]==(200 if public_read else 401)
    assert get('/api/datasets')[0]==(200 if public_read else 401)
    if public_read:
        code,body=get('/api/live/status');anonymous=json.loads(body)
        assert code==200 and anonymous['read_only'] and anonymous['csrf'] is None
        assert get('/admin')[0]==401
    assert get('/dashboard',True)[0]==200
    assert get('/realtime',True)[0]==200
    code,body=get('/api/live/status',True);state=json.loads(body)
    assert code==200 and state['hosted'] and password not in body.decode()
    return dict(state='HEALTH_AUTH_AND_HOSTED_API_VERIFIED',public_introduction=True,public_read=public_read)


if __name__=='__main__':
    print(json.dumps(check(os.environ['MONITOR_CHECK_ORIGIN'],os.environ['MONITOR_WEB_PASSWORD'],
                           public_read=os.getenv('MONITOR_PUBLIC_READ','false').lower()=='true')))
