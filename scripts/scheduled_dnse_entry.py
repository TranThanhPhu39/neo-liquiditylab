"""Windowless Task Scheduler entry with a local operational log."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def main():
    root=Path(__file__).resolve().parents[1]
    logs=root/'report/automation/logs';logs.mkdir(parents=True,exist_ok=True)
    target=logs/('dnse_'+datetime.now().strftime('%Y-%m-%d_%H%M%S_%f')+'.log')
    command=[str(Path(sys.executable).with_name('python.exe')),'-X','utf8','-m','scripts.scheduled_dnse_day',*sys.argv[1:]]
    with target.open('w',encoding='utf-8') as output:
        options=dict(cwd=root,stdout=output,stderr=subprocess.STDOUT,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        collection=subprocess.run(command,**options)
        if not sys.argv[1:]:
            # A failed date still needs an audit. Keep the scheduler's collection
            # exit code distinct from whether its reporting process ran.
            try:
                audit=subprocess.run(command[:4]+['scripts.audit_daily_delivery','--scheduled'],timeout=600,**options)
                if audit.returncode:
                    return collection.returncode or audit.returncode
            except subprocess.TimeoutExpired:
                output.write('DAILY_DELIVERY_AUDIT_TIMEOUT\n')
                return collection.returncode or 1
        return collection.returncode


if __name__=='__main__':
    raise SystemExit(main())
