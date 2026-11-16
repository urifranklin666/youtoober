#!/usr/bin/env python3
"""Install this checkout for the current desktop user; no root required."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
launcher = HOME / '.local/bin/ytm'
unit = HOME / '.config/systemd/user/ytmusic-cli.service'
plugin = HOME / '.config/obs-studio/plugins/ytmusic-obs/bin/64bit/ytmusic-obs.so'
python = ROOT / '.venv/bin/python'
if not python.exists():
    raise SystemExit('Create .venv and install this project first; see README.md')
subprocess.run(['cmake','-S',str(ROOT/'obs'),'-B',str(ROOT/'build/obs')],check=True)
subprocess.run(['cmake','--build',str(ROOT/'build/obs'),'--parallel'],check=True)
owned = launcher.exists() and 'ytmusic_cli.main' in launcher.read_text()
for path in (launcher,unit,plugin):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and not owned:
        backup = path.with_name(path.name + '.before-ytmusic')
        if not backup.exists():
            shutil.copy2(path, backup)
launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(python))+' -m ytmusic_cli.main "$@"\n')
launcher.chmod(0o755)
def systemd_quote(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
unit.write_text('[Unit]\nDescription=YouTube Music terminal player\nAfter=graphical-session.target\n\n'
                '[Service]\nType=simple\nExecStart='+systemd_quote(python)+' -m ytmusic_cli.main service run\n'
                'WorkingDirectory='+str(ROOT).replace('%','%%')+'\nUMask=0077\nRestart=on-failure\nRestartSec=2\n'
                'TimeoutStopSec=10\nEnvironment=PYTHONUNBUFFERED=1\n')
temporary_plugin = plugin.with_suffix('.so.new')
temporary_plugin.write_bytes((ROOT/'build/obs/ytmusic-obs.so').read_bytes())
temporary_plugin.replace(plugin)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
print('Installed ytm, user service, and native OBS plugin. Restart OBS to load the plugin.')
print('Authenticate in your terminal: ytm auth')
print('Start terminal interface: ytm')
print('The service is started on demand; login autostart is not enabled.')
