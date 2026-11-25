"""Measure read-only web operations against a verified local seed, without SSI keys."""
import argparse
import gc
import json
import os
from pathlib import Path
import time


def memory():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        psapi = ctypes.WinDLL('psapi')
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters();counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError('PROCESS_MEMORY_QUERY_FAILED')
        return dict(rss=counters.WorkingSetSize, peak=counters.PeakWorkingSetSize)
    fields = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith(('VmRSS:', 'VmHWM:')):
            key, value, _ = line.split();fields[key[:-1]] = int(value) * 1024
    return dict(rss=fields['VmRSS'], peak=fields['VmHWM'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--phase', choices=['catalog', 'watch', 'library', 'market', 'all'], default='all')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    from config.settings import ROOT
    root = args.root or Path(json.loads((ROOT/'report/automation/monitor_daily_publication.json').read_bytes())['stage'])
    steps = []
    def mark(name, **fields):
        value = dict(phase=name, **memory(), **fields);steps.append(value);print(json.dumps(value), flush=True)
    mark('start')
    import pyarrow as pa
    pa.set_cpu_count(1);pa.set_io_thread_count(1)
    from scripts.serve_monitor_cloud import CloudServer
    from src.monitoring.market import MarketHistory
    server = CloudServer.__new__(CloudServer)
    server.root = root.resolve();server.data = server.root/'data/exports/orderbook-monitor'
    server.market = MarketHistory(server.root)
    mark('imports')
    started = time.monotonic()
    entries = server.index();day = max(e['day'] for e in entries)
    mark('catalog', entries=len(entries), seconds=round(time.monotonic()-started, 2))
    if args.phase in ('watch', 'all'):
        value = server.watchlist(day, day+'T14:00:00+07:00', ['ACB', 'FPT', 'HPG'])
        mark('watch_primary', rows=len(value));del value;gc.collect()
        symbols = [e['symbol'] for e in entries if e['day'] == day and e.get('market_observation') and e.get('observations')][:50]
        value = server.watchlist(day, day+'T14:00:00+07:00', symbols)
        mark('watch_market_50', rows=len(value));del value;gc.collect()
    if args.phase in ('library', 'all'):
        value = server.library(include_reviews=False)
        mark('library', events=len(value));del value;gc.collect()
    if args.phase in ('market', 'all'):
        entry = next(e for e in entries if e['day'] == day and e.get('market_observation') and e.get('observations'))
        metadata = server.market.metadata(entry)
        count = sum(1 for _ in server.market.frames(entry))
        mark('market_replay', observations=count);del metadata;gc.collect()
    mark('finished')
    if args.output:
        args.output.write_text(json.dumps(steps, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
