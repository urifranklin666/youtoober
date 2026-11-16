import asyncio
import copy
import json
import random
import uuid
from .backend import Backend
from .catalog import Catalog
from .paths import STATE, save_json


class Player:
    def __init__(self, catalog=None, backend_factory=Backend, state_path=None):
        self.catalog = catalog or Catalog()
        self.backend = backend_factory(self.event)
        self.state_path = state_path or STATE / 'session.json'
        self.queue = []
        self.index = -1
        self.status = 'Stopped'
        self.position = 0
        self.duration = 0
        self.volume = 100
        self.shuffle = False
        self.repeat = 'off'
        self.error = ''
        self.history = []
        self.remaining = []
        self.load_task = None
        self.loaded = False
        self.closing = False
        self.load_session()

    def load_session(self):
        try:
            state = json.loads(self.state_path.read_text())
            self.queue = state['queue']
            self.index = int(state['index'])
            if not -1 <= self.index < len(self.queue):
                self.index = -1
            self.position = max(0, float(state.get('position', 0)))
            self.volume = max(0, min(100, float(state.get('volume', 100))))
            self.shuffle = bool(state.get('shuffle', False))
            self.repeat = state.get('repeat', 'off') if state.get('repeat') in ('off', 'one', 'all') else 'off'
            self.status = 'Paused' if self.index >= 0 else 'Stopped'
            self.duration = self.track.get('duration', 0)
        except (OSError, ValueError, TypeError, KeyError):
            self.queue = []
            self.index = -1

    @property
    def track(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else {}

    def snapshot(self):
        return copy.deepcopy({'queue': self.queue, 'index': self.index, 'track': self.track,
                              'status': self.status, 'position': self.position, 'duration': self.duration,
                              'volume': self.volume, 'shuffle': self.shuffle, 'repeat': self.repeat,
                              'error': self.error, 'connected': True})

    def persist(self):
        save_json(self.state_path, self.snapshot())

    def event(self, message):
        if self.closing:
            return
        event = message.get('event')
        if event == 'property-change':
            value = message.get('data')
            name = message.get('name')
            if name == 'time-pos' and value is not None and self.loaded:
                self.position = max(0, value)
            elif name == 'duration' and value is not None:
                self.duration = max(0, value)
            elif name == 'volume' and value is not None:
                self.volume = max(0, min(100, value))
            elif name == 'pause' and self.loaded:
                self.status = 'Paused' if value else 'Playing'
        elif event == 'file-loaded':
            self.loaded = True
        elif event == 'end-file' and self.loaded:
            self.loaded = False
            if message.get('reason') == 'eof':
                asyncio.create_task(self.advance(automatic=True))
            elif message.get('reason') == 'error':
                self.status = 'Error'
                self.error = 'Playback failed. Resume to retry this song, or skip it.'
                self.persist()
        elif event == 'disconnected':
            self.loaded = False
            self.status = 'Error'
            self.error = 'mpv disconnected. Restart the player service.'
            self.persist()

    async def cancel_load(self):
        if self.load_task and not self.load_task.done():
            self.load_task.cancel()
            await asyncio.gather(self.load_task, return_exceptions=True)
        self.load_task = None

    async def select(self, index, position=0, paused=False):
        if not 0 <= index < len(self.queue):
            raise ValueError('Queue index is out of range')
        await self.cancel_load()
        self.loaded = False
        await self.backend.command('stop')
        self.index = index
        self.position = position
        self.duration = self.track.get('duration', 0)
        self.error = ''
        self.status = 'Loading'
        identity = self.track['id']
        async def load():
            try:
                data = await self.backend.resolve(dict(self.track))
                if self.track.get('id') != identity:
                    return
                self.track.update(title=data.get('track') or data.get('title') or self.track['title'],
                                  artist=data.get('artist') or data.get('uploader') or self.track['artist'],
                                  duration=data.get('duration') or self.track.get('duration', 0),
                                  thumbnail=data.get('thumbnail') or self.track.get('thumbnail', ''))
                self.track['artwork'] = await self.catalog.artwork(dict(self.track))
                await self.backend.load(data, paused, position, self.volume)
                self.duration = self.track.get('duration', 0)
                self.status = 'Paused' if paused else 'Playing'
                self.persist()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.status = 'Error'
                self.error = str(error)
                self.persist()
        self.load_task = asyncio.create_task(load())
        self.persist()

    async def advance(self, automatic=False, backwards=False):
        if not self.queue:
            return
        if automatic and self.repeat == 'one':
            await self.select(self.index)
            return
        if backwards and self.position > 3:
            await self.dispatch('seek', {'value': 0})
            return
        current = self.track.get('id')
        if backwards and self.shuffle:
            identity = self.history.pop() if self.history else current
            target = next((i for i, t in enumerate(self.queue) if t['id'] == identity), self.index)
        elif self.shuffle:
            candidates = [t['id'] for t in self.queue if t['id'] != current]
            self.remaining = [i for i in self.remaining if i in candidates]
            if not self.remaining and (not automatic or self.repeat == 'all' or not self.history):
                self.remaining = candidates
                random.shuffle(self.remaining)
            if not self.remaining:
                if self.repeat == 'all':
                    target = self.index
                else:
                    await self.dispatch('stop', {})
                    return
            else:
                identity = self.remaining.pop()
                target = next(i for i, t in enumerate(self.queue) if t['id'] == identity)
                self.history.append(current)
        else:
            target = self.index + (-1 if backwards else 1)
            if target >= len(self.queue):
                if self.repeat == 'all':
                    target = 0
                else:
                    await self.dispatch('stop', {})
                    return
            target = max(0, target)
        await self.select(target)

    async def dispatch(self, method, params):
        if method in ('status', 'queue'):
            return self.snapshot() if method == 'status' else copy.deepcopy(self.queue)
        if method in ('search', 'library', 'liked', 'playlist', 'album'):
            return await self.catalog.call(method, params)
        if method in ('play', 'enqueue'):
            tracks = await self.catalog.tracks(params)
            if not tracks:
                raise ValueError('No playable songs in this selection')
            start = len(self.queue)
            for track in tracks:
                self.queue.append(dict(track, id=uuid.uuid4().hex))
            self.remaining = []
            if method == 'play':
                await self.select(start)
            self.persist()
            return self.snapshot()
        if method == 'select':
            await self.select(int(params['index']))
        elif method in ('next', 'previous'):
            await self.advance(backwards=method == 'previous')
        elif method == 'remove':
            index = int(params['index'])
            if not 0 <= index < len(self.queue):
                raise ValueError('Queue index is out of range')
            active = index == self.index
            if active:
                await self.dispatch('stop', {})
            self.queue.pop(index)
            if index < self.index:
                self.index -= 1
            elif active:
                self.index = -1
                self.position = self.duration = 0
        elif method == 'move':
            source, target = int(params['index']), int(params['target'])
            if not (0 <= source < len(self.queue) and 0 <= target < len(self.queue)):
                raise ValueError('Queue index is out of range')
            current = self.track.get('id')
            self.queue.insert(target, self.queue.pop(source))
            self.index = next((i for i,t in enumerate(self.queue) if t['id'] == current), -1)
        elif method == 'clear':
            await self.dispatch('stop', {})
            self.queue.clear()
            self.index = -1
            self.duration = 0
            self.remaining.clear()
            self.history.clear()
        elif method == 'shuffle':
            self.shuffle = params.get('value', 'toggle') in (True, 'on', 'true') if params.get('value', 'toggle') != 'toggle' else not self.shuffle
            self.remaining.clear()
            self.history.clear()
        elif method == 'repeat':
            value = params.get('value', 'off')
            if value not in ('off', 'one', 'all'):
                raise ValueError('Repeat must be off, one, or all')
            self.repeat = value
        elif method == 'pause':
            if self.loaded:
                await self.backend.command('set_property', 'pause', True)
                self.status = 'Paused'
            elif self.status == 'Loading':
                await self.cancel_load()
                self.status = 'Paused'
        elif method == 'resume':
            if not self.queue:
                raise ValueError('Queue is empty')
            if not self.loaded:
                await self.select(max(0, self.index), self.position)
            else:
                await self.backend.command('set_property', 'pause', False)
                self.status = 'Playing'
        elif method == 'stop':
            await self.cancel_load()
            self.loaded = False
            await self.backend.command('stop')
            self.status = 'Stopped'
            self.position = 0
        elif method == 'seek':
            value = max(0, float(params['value']))
            if self.duration:
                value = min(value, self.duration)
            if self.loaded:
                await self.backend.command('seek', value, 'absolute')
            self.position = value
        elif method == 'volume':
            self.volume = max(0, min(100, float(params['value'])))
            await self.backend.command('set_property', 'volume', self.volume)
        else:
            raise ValueError('Unknown method')
        self.persist()
        return self.snapshot()

    async def close(self):
        self.closing = True
        await self.cancel_load()
        self.persist()
        await self.backend.close()
