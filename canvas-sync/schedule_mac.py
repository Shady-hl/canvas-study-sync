#!/usr/bin/env python3
"""Opt-in macOS scheduler for canvas_sync.py; no tokens are written into the job."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'local.personal-canvas-sync'


def run():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['install', 'remove'])
    p.add_argument('--hours', type=int, default=6)
    p.add_argument('--base-url', help='Canvas HTTPS origin; reused from an existing archive when omitted.')
    p.add_argument('--output', type=Path, default=Path.home() / 'Documents' / 'Canvas-Archive')
    p.add_argument('--course', action='append', type=int)
    args = p.parse_args()
    if sys.platform != 'darwin':
        p.error('This optional helper is for macOS only. Manual syncing works on other systems.')
    if args.hours < 1:
        p.error('Use an interval of at least one hour; six hours is the default.')
    target = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    if args.action == 'remove':
        subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
        target.unlink(missing_ok=True)
        print('Automatic syncing removed. Downloaded course files are untouched.')
        return
    from archive_config import canvas_origin
    args.base_url = canvas_origin(args.output.expanduser().resolve(), args.base_url)
    # Confirm that an unattended job can retrieve the locally stored token.
    # Do not echo token data or write it to the plist.
    token_check = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', 'personal-canvas-sync', '-a', args.base_url.rstrip('/'), '-w'], capture_output=True)
    if token_check.returncode != 0:
        p.error('First create the Keychain password described in README.md, then run a manual sync.')
    token_check = None
    script = Path(__file__).resolve().with_name('canvas_sync.py')
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = Path.home() / 'Library' / 'Logs' / 'CanvasSync'
    logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(script), '--base-url', args.base_url.rstrip('/'), '--output', str(output), '--non-interactive']
    for cid in args.course or []:
        command.extend(['--course', str(cid)])
    job = {'Label': LABEL, 'ProgramArguments': command,
           'StartInterval': args.hours * 3600, 'RunAtLoad': False,
           'WorkingDirectory': str(script.parent),
           'StandardOutPath': str(logs / 'sync.log'),
           'StandardErrorPath': str(logs / 'errors.log'),
           'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'}}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    target.write_bytes(plistlib.dumps(job))
    result = subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(target)], capture_output=True, text=True)
    if result.returncode:
        print('Could not load the schedule: ' + result.stderr.strip(), file=sys.stderr)
        sys.exit(1)
    print(f'Installed: sync every {args.hours} hours while your Mac can run the job.')
    print(f'Keep this package at: {script.parent}')
    print(f'Logs: {logs}')
    print('No sync was started by the installer. Sleeping, logged-out or powered-off Macs cannot sync normally.')


if __name__ == '__main__':
    run()
