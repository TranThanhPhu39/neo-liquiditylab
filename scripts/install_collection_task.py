"""Register/inspect the current user's task without changing ExecutionPolicy."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only",action="store_true")
    args=parser.parse_args()
    script_path=Path(__file__).with_suffix(".ps1")
    script=script_path.read_text(encoding="utf-8")
    # -Command uses native ScheduledTasks cmdlets. No Bypass/Unrestricted flag or policy changes.
    command="[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & {\n"+script+"\n} "+("-VerifyOnly" if args.verify_only else "")
    # PSScriptRoot is empty for an inline command; replace only this project's path assignment.
    escaped=str(script_path.parent).replace("'","''")
    command=command.replace("$projectRoot = Split-Path -Parent $PSScriptRoot",
                            "$projectRoot = Split-Path -Parent '"+escaped+"'")
    result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],
                          cwd=script_path.parents[1],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if result.stdout:
        print(result.stdout)
    if result.returncode:
        # Registration errors are administrative diagnostics, never SDK/auth logs.
        print(result.stderr,file=sys.stderr)
    return result.returncode


if __name__=="__main__":
    raise SystemExit(main())
