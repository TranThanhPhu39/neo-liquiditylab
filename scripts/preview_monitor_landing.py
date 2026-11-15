"""Render only the public landing page in a disposable headless Edge profile."""
import base64
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]


def preview(url=None):
    if url and url not in ('https://neo-liquiditylab-monitor.onrender.com', 'https://neo-liquiditylab-monitor.onrender.com/'):
        raise ValueError('PUBLIC_LANDING_ORIGIN_REQUIRED')
    profile = ROOT / 'data/control/public-preview-cdp'
    stack = ExitStack()
    edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    process = subprocess.Popen([edge, '--headless=new', '--disable-gpu', '--no-first-run',
                                '--no-default-browser-check', '--disable-extensions', '--remote-debugging-port=0',
                                '--user-data-dir=' + str(profile), 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    socket = None
    try:
        port_file = profile / 'DevToolsActivePort'
        for _ in range(100):
            if port_file.is_file():
                break
            time.sleep(.1)
        port = int(port_file.read_text().splitlines()[0])
        tabs = json.load(urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5))
        page = next(tab for tab in tabs if tab['type'] == 'page')
        socket = stack.enter_context(connect(page['webSocketDebuggerUrl'], open_timeout=5))
        counter = 0

        def call(method, params=None):
            nonlocal counter
            counter += 1
            socket.send(json.dumps(dict(id=counter, method=method, params=params or {})))
            while True:
                value = json.loads(socket.recv(timeout=15))
                if value.get('id') == counter:
                    if 'error' in value:
                        raise RuntimeError('LANDING_BROWSER_COMMAND_FAILED')
                    return value.get('result', {})

        results = []
        for name, width in [('desktop', 1440), ('mobile', 390)]:
            call('Emulation.setDeviceMetricsOverride', dict(width=width, height=1000, deviceScaleFactor=1, mobile=name=='mobile'))
            call('Page.navigate', dict(url=url or (ROOT / 'web/monitor/landing.html').as_uri()))
            for _ in range(80):
                ready=call('Runtime.evaluate', dict(expression='document.readyState === "complete" && document.title.includes("LiquidityLab")', returnByValue=True))
                if ready['result'].get('value'):
                    break
                time.sleep(.25)
            layout = call('Runtime.evaluate', dict(expression='JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth})', returnByValue=True))
            dimensions = json.loads(layout['result']['value'])
            assert dimensions['width'] == width and dimensions['scroll'] <= width, dimensions
            image = call('Page.captureScreenshot', dict(format='png', captureBeyondViewport=True,
                         clip=dict(x=0, y=0, width=width, height=1800, scale=1)))
            (ROOT / 'report' / ('public_landing_' + name + '.png')).write_bytes(base64.b64decode(image['data']))
            results.append(dict(view=name, width=width, horizontal_overflow=False))
        return results
    finally:
        if socket:
            try:
                socket.send(json.dumps(dict(id=9999, method='Browser.close')))
            except Exception:
                pass
            stack.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url')
    args=parser.parse_args();print(json.dumps(preview(args.url)))
