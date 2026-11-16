import os
from pathlib import Path

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'ytmusic-cli'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'ytmusic-cli'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ytmusic-cli'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/ytmusic-{os.getuid()}'))
SOCKET = RUNTIME / f'ytmusic-cli-{os.getuid()}.sock'


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def save_json(path, value):
    import json
    private_dir(path.parent)
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.session-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream)
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
