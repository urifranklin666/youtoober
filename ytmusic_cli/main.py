"""Terminal entry point and user-service lifecycle."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from .paths import CONFIG, STATE, private_dir, save_json
from .service import request, serve
from .player import Player  # Public import retained for existing clients.


async def ensure_service():
    try:
        await request('status', timeout=3)
        return
    except OSError:
        pass
    unit = Path.home() / '.config/systemd/user/ytmusic-cli.service'
    if unit.exists():
        process = await asyncio.create_subprocess_exec('systemctl', '--user', 'start', 'ytmusic-cli.service',
                                                      stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await process.wait() != 0:
            raise RuntimeError('Cannot start user service. Check journalctl --user -u ytmusic-cli')
    else:
        private_dir(STATE)
        with (STATE / 'service.log').open('a') as log:
            subprocess.Popen([sys.executable, '-m', 'ytmusic_cli.main', 'service', 'run'],
                             start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             env=dict(os.environ, PYTHONUNBUFFERED='1'))
    for _ in range(100):
        await asyncio.sleep(.1)
        try:
            await request('status', timeout=3)
            return
        except OSError:
            pass
    raise RuntimeError(f'Player service failed to start. Check {STATE / "service.log"}')


def auth(args):
    private_dir(CONFIG)
    if args.cookies_browser or args.cookies_file:
        settings = {}
        if args.cookies_browser:
            settings['cookies_browser'] = args.cookies_browser
        else:
            source = Path(args.cookies_file).expanduser().resolve()
            target = CONFIG / 'cookies.txt'
            with open(target, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                stream.write(source.read_text())
            target.chmod(0o600)
            settings['cookies_file'] = str(target)
        save_json(CONFIG / 'playback.json', settings)
        print('Playback cookie source saved. No browser window is needed during playback.')
        return
    from .auth import parse_headers
    if args.headers_file:
        raw = Path(args.headers_file).expanduser().read_text()
    else:
        print('Paste YouTube Music request headers, then press Ctrl+D.\n'
              'You can also import a saved file: ytm auth --headers-file ~/isthisright')
        raw = sys.stdin.read()
    parsed = parse_headers(raw)
    save_json(CONFIG / 'browser.json', parsed)
    print('Library authentication saved.')


def build_parser():
    parser = argparse.ArgumentParser(description='YouTube Music terminal player and OBS companion')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('tui')
    p = sub.add_parser('auth')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--headers-file', metavar='FILE', help='Import saved request headers or a POST fetch snippet without pasting')
    group.add_argument('--cookies-browser', metavar='BROWSER', help='Explicitly allow yt-dlp to read this browser cookie profile')
    group.add_argument('--cookies-file', metavar='FILE', help='Import a Netscape cookie file')
    p = sub.add_parser('service')
    p.add_argument('action', nargs='?', default='start', choices=['start','stop','status','run'])
    p = sub.add_parser('search')
    p.add_argument('query', nargs='+')
    p.add_argument('--filter', choices=['songs','albums'], default='songs')
    p = sub.add_parser('library')
    p.add_argument('kind', nargs='?', default='playlists', choices=['playlists','albums','songs'])
    for name in ('playlist','album'):
        p = sub.add_parser(name)
        p.add_argument('id')
    for name in ('play','enqueue'):
        p = sub.add_parser(name)
        p.add_argument('url', nargs='?')
        p.add_argument('--playlist', dest='playlist_id')
        p.add_argument('--album', dest='album_id')
        p.add_argument('--liked', action='store_true')
    for name in ('status','queue','liked','pause','resume','stop','next','previous','clear'):
        sub.add_parser(name)
    for name in ('select','remove'):
        p = sub.add_parser(name)
        p.add_argument('index', type=int, help='Zero-based queue index')
    p = sub.add_parser('move')
    p.add_argument('index', type=int)
    p.add_argument('target', type=int)
    for name in ('seek','volume'):
        p = sub.add_parser(name)
        p.add_argument('value', type=float)
    p = sub.add_parser('shuffle')
    p.add_argument('value', nargs='?', default='toggle', choices=['on','off','toggle'])
    p = sub.add_parser('repeat')
    p.add_argument('value', choices=['off','one','all'])
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == 'auth':
            auth(args)
            return
        if args.command == 'service' and args.action == 'run':
            asyncio.run(serve())
            return
        if args.command in (None, 'tui'):
            from .ui import MusicApp
            MusicApp().run()
            return
        async def run():
            if args.command == 'service':
                if args.action == 'start':
                    await ensure_service()
                    print('Player service is running')
                elif args.action == 'stop':
                    try:
                        await request('shutdown')
                    except (FileNotFoundError, ConnectionRefusedError):
                        pass
                else:
                    print(json.dumps(await request('status'), indent=2))
                return
            await ensure_service()
            params = {key: value for key, value in vars(args).items() if key != 'command'}
            if args.command == 'search':
                params['query'] = ' '.join(args.query)
            if args.command in ('play','enqueue'):
                if args.liked:
                    params = {'tracks': await request('liked')}
                elif args.playlist_id:
                    params = {'kind': 'playlist', 'id': args.playlist_id}
                elif args.album_id:
                    params = {'kind': 'album', 'id': args.album_id}
                elif not args.url:
                    raise ValueError('Provide a URL, --playlist ID, --album ID, or --liked')
            print(json.dumps(await request(args.command, params), indent=2, ensure_ascii=False))
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
