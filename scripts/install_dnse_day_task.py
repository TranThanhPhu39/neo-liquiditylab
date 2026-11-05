"""Install the authorized DNSE local fallback without changing ExecutionPolicy."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    path=Path(__file__).with_suffix('.ps1');script=path.read_text(encoding='utf-8')
    escaped=str(path.parent).replace("'","''")
    script=script.replace('$projectRoot = Split-Path -Parent $PSScriptRoot',"$projectRoot = Split-Path -Parent '"+escaped+"'")
    command='[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & {\n'+script+'\n} '+('-VerifyOnly' if args.verify_only else '')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],cwd=path.parents[1],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.stdout: print(result.stdout)
    if result.returncode: print(result.stderr,file=sys.stderr)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
