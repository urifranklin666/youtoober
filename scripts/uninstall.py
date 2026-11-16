#!/usr/bin/env python3
"""Remove installed components while preserving credentials and saved sessions."""
from pathlib import Path
import subprocess
home = Path.home()
subprocess.run(['systemctl','--user','stop','ytmusic-cli.service'],check=False)
for path in (home/'.local/bin/ytm',home/'.config/systemd/user/ytmusic-cli.service',
             home/'.config/obs-studio/plugins/ytmusic-obs/bin/64bit/ytmusic-obs.so'):
    path.unlink(missing_ok=True)
    backup = path.with_name(path.name+'.before-ytmusic')
    if backup.exists():
        backup.replace(path)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
print('Removed installed components. Credentials, cached artwork, and saved sessions were preserved.')
