"""Private mpv JSON IPC and cancellable yt-dlp extraction."""
import asyncio
import json
from .paths import CONFIG, SOCKET


class Backend:
    def __init__(self, event):
        self.event = event
        self.process = None
        self.reader = None
        self.writer = None
        self.read_task = None
        self.pending = {}
        self.sequence = 0
        self.extractor = None

    async def start(self, audio_output=None):
        path = SOCKET.with_suffix('.mpv')
        path.unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            'mpv', '--no-config', '--no-video', '--idle=yes', '--pause=yes', '--really-quiet',
            '--ytdl=no', f'--input-ipc-server={path}', '--terminal=no',
            *([f'--ao={audio_output}'] if audio_output else []),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(path)
                break
            except OSError:
                if self.process.returncode is not None:
                    raise RuntimeError('mpv could not start')
                await asyncio.sleep(.05)
        else:
            raise RuntimeError('mpv IPC did not become ready')
        self.read_task = asyncio.create_task(self.read())
        for index, prop in enumerate(('pause', 'time-pos', 'duration', 'volume')):
            await self.command('observe_property', index, prop)

    async def read(self):
        try:
            while line := await self.reader.readline():
                message = json.loads(line)
                future = self.pending.get(message.get('request_id'))
                if future is not None and not future.done():
                    future.set_result(message)
                elif 'event' in message:
                    self.event(message)
        except (OSError, ValueError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('mpv disconnected'))
            self.event({'event': 'disconnected'})

    async def command(self, *command):
        if not self.writer:
            raise RuntimeError('mpv is not running')
        self.sequence += 1
        identity = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            self.writer.write((json.dumps({'command': command, 'request_id': identity})+'\n').encode())
            await self.writer.drain()
            result = await asyncio.wait_for(future, 5)
            if result.get('error') != 'success':
                raise RuntimeError('Playback command failed: ' + str(result.get('error')))
            return result.get('data')
        finally:
            self.pending.pop(identity, None)

    async def resolve(self, track):
        options = []
        settings = CONFIG / 'playback.json'
        if settings.exists():
            settings = json.loads(settings.read_text())
            if settings.get('cookies_browser'):
                options += ['--cookies-from-browser', settings['cookies_browser']]
            elif settings.get('cookies_file'):
                options += ['--cookies', settings['cookies_file']]
        for attempt in range(2):
            process = await asyncio.create_subprocess_exec(
                'yt-dlp', '--ignore-config', '--no-playlist', '--no-warnings', '--skip-download',
                '--dump-single-json', '--extractor-args', 'youtube:player-client=default,web_safari',
                '-f', 'bestaudio[protocol*=m3u8]/worst[protocol*=m3u8]/bestaudio/best', '--socket-timeout', '15',
                *options, '--', track['url'], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=8_000_000)
            self.extractor = process
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 60)
                if process.returncode == 0:
                    data = json.loads(output)
                    if data.get('url', '').startswith('https://'):
                        return data
            except asyncio.CancelledError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            except (TimeoutError, ValueError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            finally:
                self.extractor = None
            if attempt == 0:
                await asyncio.sleep(1)
        raise RuntimeError('Cannot resolve this song. Check your connection, update yt-dlp, or configure playback cookies with ytm auth --cookies-browser chrome.')

    async def load(self, data, paused=False, position=0, volume=100):
        # HTTP headers remain private to mpv and never enter the public service state.
        headers = [f'{key}: {value}' for key, value in data.get('http_headers', {}).items()]
        await self.command('set_property', 'http-header-fields', headers)
        await self.command('set_property', 'volume', volume)
        await self.command('set_property', 'pause', paused)
        await self.command('loadfile', data['url'], 'replace', -1, {'start': str(position)})

    async def close(self):
        if self.extractor and self.extractor.returncode is None:
            self.extractor.kill()
        if self.writer:
            self.writer.close()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self.read_task:
            self.read_task.cancel()
            await asyncio.gather(self.read_task, return_exceptions=True)
        SOCKET.with_suffix('.mpv').unlink(missing_ok=True)
