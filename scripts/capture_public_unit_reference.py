"""Read the anonymous Maybank price board in an isolated browser for unit evidence."""
from contextlib import ExitStack
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import re
import time
from urllib.request import urlopen
import uuid

from websockets.sync.client import connect

ROOT=Path(__file__).resolve().parents[1]
URL='https://priceboard.maybank-kimeng.com.vn/?lang=vi-vn'


def main(symbols=('ACB','FPT','HPG')):
    symbols=tuple(dict.fromkeys(symbols))
    if not symbols or len(symbols)>50 or any(not re.fullmatch(r'[A-Z][A-Z0-9]{1,11}',s) for s in symbols):
        raise ValueError('REFERENCE_SYMBOL_SCOPE_INVALID')
    # Keep the actual visible rows/caption; do not infer a missing row or venue.
    expression="JSON.stringify({title:document.title,url:location.href,text:document.body.innerText.slice(0,250000),rows:[...document.querySelectorAll('tr')].filter(e=>"+json.dumps(list(symbols))+".includes(e.querySelector('td')?.innerText.trim())).map(e=>({text:e.innerText,cells:[...e.querySelectorAll('td')].map(x=>x.innerText)}))})"
    folder=ROOT/'data/raw/ssi'/('maybank_reference_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    folder.mkdir(parents=True)
    profile=ROOT/'data/control'/('unit-reference-browser-'+uuid.uuid4().hex)
    process=subprocess.Popen(['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','--headless=new',
        '--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions',
        '--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    with ExitStack() as stack:
        socket=None
        try:
            portfile=profile/'DevToolsActivePort'
            for _ in range(100):
                if portfile.exists():break
                time.sleep(.1)
            port=int(portfile.read_text().splitlines()[0])
            pages=json.load(urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5))
            socket=stack.enter_context(connect(next(x for x in pages if x['type']=='page')['webSocketDebuggerUrl']))
            serial=0
            def call(method,params):
                nonlocal serial
                serial+=1;socket.send(json.dumps(dict(id=serial,method=method,params=params)))
                while True:
                    r=json.loads(socket.recv(timeout=20))
                    if r.get('id')==serial:return r.get('result',{})
            call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
            call('Page.navigate',dict(url=URL));time.sleep(15)
            samples=[]
            for page_number in range(1,12):
                if page_number>1:
                    action=call('Runtime.evaluate',dict(expression="(()=>{const e=[...document.querySelectorAll('a,button,[role=button]')].find(e=>e.innerText.trim()==="+json.dumps(str(page_number))+"&&e.getClientRects().length);if(e){e.click();return true;}return false;})()",returnByValue=True))
                    time.sleep(2)
                r=call('Runtime.evaluate',dict(expression=expression,returnByValue=True))
                value=json.loads(r['result']['value']);value['received_at']=datetime.now(timezone.utc).isoformat();value['requested_page']=page_number;samples.append(value)
                if value['rows']:
                    for _ in range(3):
                        time.sleep(2)
                        r=call('Runtime.evaluate',dict(expression=expression,returnByValue=True))
                        again=json.loads(r['result']['value']);again.update(received_at=datetime.now(timezone.utc).isoformat(),requested_page=page_number);samples.append(again)
                time.sleep(1)
            captured=sorted({row['cells'][0].strip() for sample in samples for row in sample['rows'] if row['cells']})
            output=folder/'observations.json';output.write_text(json.dumps(dict(url=URL,requested_symbols=list(symbols),captured_symbols=captured,missing_symbols=sorted(set(symbols)-set(captured)),samples=samples),ensure_ascii=False),encoding='utf-8')
            print(json.dumps(dict(evidence=output.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                titles=[s['title'] for s in samples],row_counts=[len(s['rows']) for s in samples],
                unit_contexts=[s['text'][-250:] for s in samples[:1]])))
        finally:
            if socket:
                try:socket.send(json.dumps(dict(id=99999,method='Browser.close')))
                except Exception:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',default='ACB,FPT,HPG',help='Up to 50 comma-separated symbols; only visible board rows can be captured.')
    args=parser.parse_args();main(args.symbols.split(','))
