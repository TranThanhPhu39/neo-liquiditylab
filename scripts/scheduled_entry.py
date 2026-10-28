"""Windowless task entry; capture a normal Python child's output into a local log."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def main():
    root=Path(__file__).resolve().parents[1]
    logs=root/"report/automation/logs"
    logs.mkdir(parents=True,exist_ok=True)
    target=logs/(datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")+".log")
    executable=Path(sys.executable).with_name("python.exe")
    command=[str(executable),"-X","utf8","-m","scripts.scheduled_collection",*sys.argv[1:]]
    with target.open("w",encoding="utf-8") as output:
        result=subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode


if __name__=="__main__":
    raise SystemExit(main())
