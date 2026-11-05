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
        return subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode


if __name__=='__main__':
    raise SystemExit(main())
