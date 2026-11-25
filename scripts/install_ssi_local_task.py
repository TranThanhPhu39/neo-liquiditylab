"""Register/verify the user-approved Windows SSI fallback, without storing a password."""
from pathlib import Path
import argparse
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    path=Path(__file__).with_suffix('.ps1')
    source=path.read_text(encoding='utf-8').replace('$projectRoot = Split-Path -Parent $PSScriptRoot',
        "$projectRoot = Split-Path -Parent '"+str(path.parent).replace("'","''")+"'")
    command='[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & {\n'+source+'\n} '+('-VerifyOnly' if args.verify_only else '')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],cwd=path.parents[1],
                          capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.stdout:print(result.stdout)
    if result.returncode:print(result.stderr,file=sys.stderr)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
