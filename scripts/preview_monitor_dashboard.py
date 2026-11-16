"""Real-browser QA of the owned app on loopback; no account/profile or broker access."""
import base64
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import urlopen
import uuid

from websockets.sync.client import connect
from config.settings import ROOT
from scripts.serve_orderbook_monitor import MonitorServer,Handler


def preview():
    current=json.loads((ROOT/'report/verified_monitor_history_latest.json').read_bytes())
    stage=ROOT/current['stage']
    class TransientHandler(Handler):
        attempts=0
        def do_GET(self):
            if self.path=='/api/datasets':
                type(self).attempts+=1
                if type(self).attempts==1:return self.send_json(dict(error='QA_TRANSIENT_BUSY'),503)
            return super().do_GET()
    server=MonitorServer(stage,port=0,assets_root=ROOT,handler_class=TransientHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    profile=ROOT/'data/control'/('dashboard-browser-qa-'+uuid.uuid4().hex)
    process=subprocess.Popen(['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new',
        '--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions','--remote-debugging-port=0',
        '--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    stack=ExitStack();ws=None;errors=[];counter=0
    try:
        port_file=profile/'DevToolsActivePort'
        for _ in range(100):
            if port_file.exists():break
            time.sleep(.1)
        port=int(port_file.read_text().splitlines()[0]);tabs=json.load(urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5))
        ws=stack.enter_context(connect(next(t for t in tabs if t['type']=='page')['webSocketDebuggerUrl'],open_timeout=5))
        def call(method,params=None):
            nonlocal counter
            counter+=1;ws.send(json.dumps(dict(id=counter,method=method,params=params or {})))
            while True:
                v=json.loads(ws.recv(timeout=30))
                if v.get('method')=='Runtime.exceptionThrown':errors.append(v['params']['exceptionDetails'].get('text','JS_EXCEPTION'))
                if v.get('id')==counter:
                    if 'error' in v:raise ValueError('BROWSER_QA_COMMAND_FAILED')
                    return v.get('result',{})
        def evaluate(expression):
            value=call('Runtime.evaluate',dict(expression=expression,returnByValue=True))
            if 'exceptionDetails' in value:raise ValueError('BROWSER_QA_SCRIPT_FAILED')
            return value['result'].get('value')
        def ready(symbol):
            for _ in range(120):
                if evaluate("document.getElementById('symbolSelect')?.value === "+json.dumps(symbol)+" && document.getElementById('play')?.disabled === false && document.getElementById('error')?.hidden === true"):return
                time.sleep(.25)
            raise ValueError('BROWSER_DATA_LOAD_FAILED_'+symbol)
        call('Runtime.enable');call('Page.enable')
        call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
        call('Page.navigate',dict(url=origin+'/'));ready('ACB')
        results=[]
        for symbol in ('ACB','FPT','HPG'):
            evaluate("document.getElementById('symbolSelect').value="+json.dumps(symbol)+";document.getElementById('symbolSelect').dispatchEvent(new Event('change',{bubbles:true}));")
            ready(symbol)
            evaluate("document.getElementById('seekTime').value='13:30:00';document.getElementById('seek').click();")
            state=json.loads(evaluate("JSON.stringify({symbol:document.getElementById('symbolSelect').value,title:document.getElementById('title').textContent,warningHidden:document.getElementById('unitWarning').hidden,labels:document.getElementById('sideWatch').textContent,canvasWidth:document.getElementById('heatmap').width,book:document.getElementById('book').textContent.length})"))
            assert state['warningHidden'] and state['canvasWidth']>0 and state['book']>0
            assert 'detector bị chặn' not in state['labels']
            results.append(state)
        image=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/dashboard_qa_desktop.png').write_bytes(base64.b64decode(image['data']))
        evaluate("document.querySelector('[data-page=watch]').click()")
        time.sleep(.7)
        assert evaluate("document.getElementById('watchScreen').hidden === false")
        evaluate("document.querySelector('[data-page=analysis]').click()")
        call('Emulation.setDeviceMetricsOverride',dict(width=390,height=1000,deviceScaleFactor=1,mobile=True))
        time.sleep(.5)
        dimensions=json.loads(evaluate('JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth})'))
        image=call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/'report/dashboard_qa_mobile.png').write_bytes(base64.b64decode(image['data']))
        result=dict(state='REAL_BROWSER_QA_PASSED' if not errors and dimensions['scroll']<=390 else 'REAL_BROWSER_QA_ISSUES',
            symbols=results,mobile=dimensions,console_exceptions=errors,get_retry_proven=TransientHandler.attempts>=2,
            broker_access=False,remote_upload=False)
        (ROOT/'report/dashboard_browser_qa_2026-11-16.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        if ws:
            try:ws.send(json.dumps(dict(id=9999,method='Browser.close')))
            except Exception:pass
        stack.close()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.terminate()
        server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':print(json.dumps(preview(),ensure_ascii=True))
