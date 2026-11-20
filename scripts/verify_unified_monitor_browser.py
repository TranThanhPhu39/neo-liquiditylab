"""Check published viewer pages in a fresh browser with no credentials or writes."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
import uuid

from websockets.sync.client import connect

ROOT=Path(__file__).resolve().parents[1]
ORIGIN='https://neo-liquiditylab-monitor.onrender.com'


def main():
    global ORIGIN
    parser=argparse.ArgumentParser();parser.add_argument('--origin',default=ORIGIN);args=parser.parse_args();ORIGIN=args.origin.rstrip('/')
    profile=ROOT/'data/control'/('unified-view-check-'+uuid.uuid4().hex)
    process=subprocess.Popen([
        'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new',
        '--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions',
        '--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    result=dict(origin=ORIGIN,anonymous=True,auth_challenges=0,write_attempts=0,js_exceptions=[],http_failures=[],wait_notices=[])
    ws=None;counter=0
    try:
        for _ in range(100):
            if (profile/'DevToolsActivePort').is_file():break
            time.sleep(.1)
        port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
        tabs=json.load(urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5))
        ws=connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),open_timeout=5,legacy=True)

        def send(method,params=None):
            nonlocal counter
            counter+=1;ws.send(json.dumps(dict(id=counter,method=method,params=params or {})));return counter

        def call(method,params=None):
            wanted=send(method,params)
            while True:
                item=json.loads(ws.recv(timeout=40));kind=item.get('method');event=item.get('params',{})
                if kind=='Fetch.authRequired':
                    result['auth_challenges']+=1
                    send('Fetch.continueWithAuth',dict(requestId=event['requestId'],authChallengeResponse=dict(response='CancelAuth')))
                elif kind=='Fetch.requestPaused':
                    if event['request']['method'] not in ('GET','HEAD','OPTIONS'):
                        result['write_attempts']+=1
                        send('Fetch.failRequest',dict(requestId=event['requestId'],errorReason='BlockedByClient'))
                    else:send('Fetch.continueRequest',dict(requestId=event['requestId']))
                elif kind=='Runtime.exceptionThrown':result['js_exceptions'].append(event['exceptionDetails'].get('text','JS_EXCEPTION'))
                elif kind=='Network.responseReceived' and event['response']['status']>=400:
                    result['http_failures'].append(dict(status=event['response']['status'],path=event['response']['url'].split(ORIGIN)[-1].split('?')[0]))
                if item.get('id')==wanted:
                    if item.get('error'):raise RuntimeError('BROWSER_COMMAND_FAILED')
                    return item.get('result',{})

        def evaluate(expression):
            value=call('Runtime.evaluate',dict(expression=expression,returnByValue=True))
            if value.get('exceptionDetails'):raise RuntimeError('BROWSER_EVALUATION_FAILED')
            return value.get('result',{}).get('value')

        def wait(condition,seconds=240):
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                notice=evaluate("document.getElementById('serverNotice')?.hidden===false?document.getElementById('serverNotice').textContent:null")
                if notice and notice not in result['wait_notices']:result['wait_notices'].append(notice)
                if evaluate(condition):return True
                time.sleep(.3)
            return False

        for method in ('Runtime.enable','Page.enable','Network.enable'):call(method)
        call('Fetch.enable',dict(handleAuthRequests=True,patterns=[dict(urlPattern=ORIGIN+'/*')]))
        call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        call('Page.navigate',dict(url=ORIGIN+'/dashboard'))
        if wait("Boolean(document.getElementById('serverNotice'))",seconds=25):
            evaluate("document.querySelector('[data-page=review]').click();")
        if not wait("document.getElementById('play')?.disabled===false && document.getElementById('error')?.hidden===true"):
            raise RuntimeError('PUBLIC_DASHBOARD_NOT_READY')
        result['dashboard']=evaluate("({day:document.getElementById('daySelect').value,symbol:document.getElementById('symbolSelect').value,frames:Number(document.getElementById('cursor').max)+1,canvas:document.getElementById('heatmap').width,config_disabled:document.getElementById('openConfig').disabled,review_hidden:document.getElementById('reviewForm').hidden})")
        evaluate("document.querySelector('[data-page=review]').click();")
        if not wait("document.querySelectorAll('#libraryTable tbody tr').length>0 && document.getElementById('error')?.hidden===true"):
            result['library_error']=evaluate("document.getElementById('libraryStatus')?.textContent")
            raise RuntimeError('PUBLIC_EVENT_LIBRARY_NOT_READY')
        result['library']=evaluate("({rows:document.querySelectorAll('#libraryTable tbody tr').length,status:document.getElementById('libraryStatus').textContent,error_hidden:document.getElementById('error').hidden,notice_hidden:document.getElementById('serverNotice').hidden})")
        screenshot=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/recovery_library_2026-11-20.png').write_bytes(base64.b64decode(screenshot['data']))
        evaluate("document.querySelector('[data-page=analysis]').click();")
        evaluate("document.getElementById('sessionEnd').click();document.querySelector('#events button')?.click();")
        result['dashboard']['event_review_hidden']=evaluate("document.getElementById('reviewForm').hidden")
        screenshot=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/unified_monitor_2026-11-20_desktop.png').write_bytes(base64.b64decode(screenshot['data']))
        call('Emulation.setDeviceMetricsOverride',dict(width=390,height=1000,deviceScaleFactor=1,mobile=True))
        time.sleep(.3)
        result['mobile']=evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        evaluate("document.getElementById('dataMode').value='capture';document.getElementById('dataMode').dispatchEvent(new Event('change',{bubbles:true}));")
        if not wait("document.getElementById('dataMode')?.value==='capture' && document.getElementById('play')?.disabled===false && document.getElementById('error')?.hidden===true"):
            result['capture_error']=evaluate("document.getElementById('error')?.textContent")
            raise RuntimeError('SHARED_CAPTURE_NOT_READY')
        result['capture']=evaluate("({url:location.pathname,mode:document.getElementById('dataMode').value,book_length:document.getElementById('book').textContent.length,run:document.getElementById('captureRun').value,review_hidden:document.getElementById('reviewForm').hidden,config_disabled:document.getElementById('openConfig').disabled,status:document.getElementById('modeStatus').textContent})")
        evaluate("document.getElementById('pauseCapture').click();")
        result['capture']['paused']=evaluate("document.getElementById('viewBadge').textContent")
        screenshot=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/unified_monitor_2026-11-20_capture.png').write_bytes(base64.b64decode(screenshot['data']))
        call('Emulation.setDeviceMetricsOverride',dict(width=390,height=1000,deviceScaleFactor=1,mobile=True))
        time.sleep(.3)
        result['capture_mobile']=evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        screenshot=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/unified_monitor_2026-11-20_mobile.png').write_bytes(base64.b64decode(screenshot['data']))
        evaluate("document.getElementById('dataMode').value='history';document.getElementById('dataMode').dispatchEvent(new Event('change',{bubbles:true}));")
        if not wait("document.getElementById('dataMode')?.value==='history' && document.getElementById('play')?.disabled===false && document.getElementById('error')?.hidden===true"):
            raise RuntimeError('RETURN_TO_HISTORY_NOT_READY')
        result['returned_to_history']=evaluate("document.getElementById('symbolSelect').value==='ACB' && document.getElementById('captureActions').hidden")
        assert result['auth_challenges']==result['write_attempts']==0
        assert not result['js_exceptions']
        assert not [r for r in result['http_failures'] if r['path']!='/favicon.ico' and r['status'] not in (429,502,503,504)]
        assert result['dashboard']['config_disabled'] and result['dashboard']['event_review_hidden']
        assert result['capture']['review_hidden'] and result['capture']['config_disabled']
        assert result['capture']['url']=='/dashboard' and result['returned_to_history']
        assert result['capture_mobile']['scroll']<=result['capture_mobile']['width']
        assert result['mobile']['scroll']<=result['mobile']['width']
        result['state']='UNIFIED_ANONYMOUS_BROWSER_VERIFIED'
    except Exception as error:
        result.update(state='CHECK_FAILED',error=str(error)[:120])
    finally:
        if ws:
            try:send('Browser.close')
            except Exception:pass
            ws.close()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.terminate()
        (ROOT/'report/unified_monitor_browser_2026-11-20.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['state']!='UNIFIED_ANONYMOUS_BROWSER_VERIFIED':raise SystemExit(1)


if __name__=='__main__':main()
