"""On-demand silent video frames. Stream URLs never leave the service process."""
import asyncio
import base64
import time
from .backend import Backend

FPS = 24
IDLE_SECONDS = 3


class Video:
    def __init__(self, player, resolver=None):
        self.player = player
        # Independent extraction prevents cancellation from interfering with audio.
        self.resolver = resolver or Backend(lambda event: None)
        self.demand = -100
        self.key = ''
        self.revision = 0
        self.jpeg = b''
        self.frame_at = -100
        self.frame_position = 0
        self.status = 'idle'
        self.task = None
        self.process = None
        self.cached = None
        self.cached_at = -100
        self.previous_position = 0
        self.previous_time = 0
        self.failed = False
        self.closing = False

    def snapshot(self, after=-1):
        self.demand = time.monotonic()
        key = self.player.track.get('id', '')
        usable = key == self.key and self.player.status in ('Playing', 'Paused')
        result = {'track_id': key, 'status': self.status if usable else 'loading',
                  'revision': self.revision, 'frame': '', 'fps': FPS}
        if usable and self.status == 'ready' and self.player.status == 'Playing' and time.monotonic()-self.frame_at > 2:
            result['status'] = 'loading'
            result['revision'] = -1
            return result
        if usable and self.status == 'ready' and self.revision != after:
            result['frame'] = base64.b64encode(self.jpeg).decode('ascii')
        return result

    def clear(self, status='loading'):
        self.jpeg = b''
        self.status = status
        self.revision += 1

    async def cancel(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    async def run(self):
        while True:
            await asyncio.sleep(.1)
            now = time.monotonic()
            key = self.player.track.get('id', '')
            active = now-self.demand < IDLE_SECONDS and key and self.player.status in ('Playing', 'Paused')
            if not active:
                if self.task:
                    await self.cancel()
                if self.key:
                    self.key = ''
                    self.cached = None
                    self.failed = False
                    self.clear('idle')
                self.previous_time = now
                continue
            position = self.player.position
            elapsed = now-self.previous_time
            delta = position-self.previous_position
            sought = self.previous_time > 0 and (delta < -.15 or delta > elapsed+.5)
            changed = key != self.key
            if changed or sought:
                await self.cancel()
                self.clear()
                if changed:
                    self.cached = None
                    self.failed = False
                self.key = key
            if self.task is None and not self.failed:
                track = dict(self.player.track)
                self.task = asyncio.create_task(self.play(track, position))
            self.previous_position, self.previous_time = position, now

    async def play(self, track, position):
        try:
            # Refresh once after a decoder/network failure, then fall back to artwork.
            for attempt in range(2):
                if self.cached is None or time.monotonic()-self.cached_at > 300:
                    self.cached = await self.resolver.resolve(track, video=True)
                    self.cached_at = time.monotonic()
                data = self.cached
                if data.get('vcodec') == 'none' or float(data.get('height') or 0) > 720:
                    raise RuntimeError('No suitable video')
                position = max(0, self.player.position)
                succeeded = await self.decode(data, position)
                if succeeded:
                    return
                self.cached = None
            self.failed = True
            self.clear('unavailable')
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not expose FFmpeg output, authentication headers, or signed URLs.
            self.failed = True
            self.clear('unavailable')

    async def present(self, jpeg, timestamp):
        while True:
            position = self.player.position
            if timestamp < position-.08:
                return  # Catch up after loading; do not display stale buffered frames.
            if timestamp > position+.02:
                await asyncio.sleep(.01)
                continue
            self.jpeg = jpeg
            self.frame_at = time.monotonic()
            self.frame_position = timestamp
            self.status = 'ready'
            self.revision += 1
            return

    async def decode(self, data, position):
        headers = {key.lower(): str(value) for key,value in data.get('http_headers', {}).items()}
        # CDN playback needs public request metadata, not account cookies.
        allowed = ('user-agent', 'accept', 'accept-language')
        header_text = ''.join(f'{key}: {headers[key]}\r\n' for key in allowed if key in headers and '\r' not in headers[key] and '\n' not in headers[key])
        options = ['-headers', header_text] if header_text else []
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'quiet', '-nostdin',
            '-ss', str(position), *options, '-i', data['url'], '-an', '-sn', '-dn',
            '-vf', f'fps={FPS},scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2',
            '-threads', '2', '-c:v', 'mjpeg', '-q:v', '4', '-f', 'image2pipe', 'pipe:1',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=2_000_000)
        self.process = process
        buffer = b''
        frame_index = 0
        try:
            while chunk := await asyncio.wait_for(process.stdout.read(65536), 15):
                buffer += chunk
                if len(buffer) > 2_000_000:
                    raise RuntimeError('Invalid video frame')
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9', start+2) if start >= 0 else -1
                    if end < 0:
                        break
                    jpeg, buffer = buffer[start:end+2], buffer[end+2:]
                    await self.present(jpeg, position+frame_index/FPS)
                    frame_index += 1
            await process.wait()
            # A normal EOF is not retried. Track advancement resets this session.
            return process.returncode == 0 and frame_index > 0
        except (TimeoutError, OSError):
            return False
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            self.process = None

    async def close(self):
        self.closing = True
        await self.cancel()
        extractor = getattr(self.resolver, 'extractor', None)
        if extractor and extractor.returncode is None:
            extractor.kill()
            await extractor.wait()
