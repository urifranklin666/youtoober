import asyncio
import tempfile
import unittest
from pathlib import Path
from ytmusic_cli.player import Player
from ytmusic_cli.catalog import Catalog, normalize, parse_url

URL = 'https://music.youtube.com/watch?v=test'


class FakeCatalog(Catalog):
    async def artwork(self, track):
        return '/tmp/art.png'


class FakeBackend:
    def __init__(self, event):
        self.event = event
        self.commands = []
        self.loads = []
        self.resolutions = 0
        self.failure = False
        self.gate = None

    async def command(self, *args):
        self.commands.append(args)

    async def resolve(self, track):
        self.resolutions += 1
        if self.gate:
            await self.gate.wait()
        if self.failure:
            raise RuntimeError('resolution failed')
        return {'url':'https://example.test/private-stream', 'title':'Resolved title', 'artist':'Artist', 'duration':100}

    async def load(self, *args):
        self.loads.append(args)
        self.event({'event':'file-loaded'})

    async def close(self):
        pass


class PlayerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'session.json'
        self.player = Player(FakeCatalog(), FakeBackend, self.path)

    async def asyncTearDown(self):
        await self.player.close()
        self.directory.cleanup()

    async def enqueue(self, count=3):
        for i in range(count):
            await self.player.dispatch('enqueue', {'url':URL+str(i)})

    async def load(self, index=0):
        await self.player.select(index)
        await self.player.load_task

    async def test_initial_and_enqueue(self):
        self.assertEqual(self.player.snapshot()['status'], 'Stopped')
        await self.enqueue(1)
        self.assertEqual(self.player.backend.resolutions, 0)
        self.assertEqual(len(self.player.queue), 1)

    async def test_queue_move_preserves_active_identity_and_remove(self):
        await self.enqueue()
        await self.load(1)
        identity = self.player.track['id']
        await self.player.dispatch('move', {'index':1,'target':0})
        self.assertEqual(self.player.index, 0)
        self.assertEqual(self.player.track['id'], identity)
        await self.player.dispatch('remove', {'index':0})
        self.assertEqual(self.player.status, 'Stopped')
        self.assertEqual(self.player.index, -1)

    async def test_repeat_and_eof(self):
        await self.enqueue(2)
        await self.load(1)
        await self.player.advance(automatic=True)
        self.assertEqual(self.player.status, 'Stopped')
        self.player.repeat = 'all'
        await self.player.advance(automatic=True)
        await self.player.load_task
        self.assertEqual(self.player.index, 0)
        self.player.repeat = 'one'
        await self.player.advance(automatic=True)
        await self.player.load_task
        self.assertEqual(self.player.index, 0)

    async def test_shuffle_does_not_repeat_before_exhaustion(self):
        await self.enqueue(5)
        await self.load()
        await self.player.dispatch('shuffle', {'value':'on'})
        identities = {self.player.track['id']}
        for _ in range(4):
            await self.player.advance(automatic=True)
            await self.player.load_task
            identities.add(self.player.track['id'])
        self.assertEqual(len(identities), 5)
        await self.player.advance(automatic=True)
        self.assertEqual(self.player.status, 'Stopped')

    async def test_restore_paused_and_redact_stream(self):
        await self.enqueue(1)
        await self.load()
        self.player.position = 42
        self.player.persist()
        restored = Player(FakeCatalog(), FakeBackend, self.path)
        self.assertEqual(restored.status, 'Paused')
        self.assertEqual(restored.position, 42)
        self.assertNotIn('private-stream', self.path.read_text())
        self.assertNotIn('private-stream', str(self.player.snapshot()))

    async def test_stop_cancels_extraction(self):
        await self.enqueue(1)
        self.player.backend.gate = asyncio.Event()
        await self.player.select(0)
        await asyncio.sleep(0)
        await self.player.dispatch('stop', {})
        self.assertEqual(self.player.status, 'Stopped')
        self.assertFalse(self.player.backend.loads)

    async def test_extraction_failure_keeps_queue(self):
        await self.enqueue(1)
        self.player.backend.failure = True
        await self.load()
        self.assertEqual(self.player.status, 'Error')
        self.assertEqual(len(self.player.queue), 1)
        self.assertEqual(self.player.error, 'resolution failed')

    async def test_clamp_seek_volume_and_clear(self):
        await self.enqueue(1)
        await self.load()
        await self.player.dispatch('seek', {'value':999})
        await self.player.dispatch('volume', {'value':-10})
        self.assertEqual(self.player.position, 100)
        self.assertEqual(self.player.volume, 0)
        await self.player.dispatch('clear', {})
        self.assertEqual(self.player.queue, [])
        self.assertEqual(self.player.duration, 0)

    async def test_no_browser_or_local_file_injection(self):
        for url in ('file:///etc/passwd','https://youtube.com.evil.test/watch?v=a','https://example.com'):
            with self.assertRaises(ValueError):
                await self.player.dispatch('play', {'url':url})

    async def test_previous_restarts_after_three_seconds(self):
        await self.enqueue(2)
        await self.load(1)
        self.player.position = 20
        await self.player.advance(backwards=True)
        self.assertEqual(self.player.index, 1)
        self.assertEqual(self.player.position, 0)

    async def test_eof_event_advances_and_error_event_preserves_track(self):
        await self.enqueue(2)
        await self.load()
        self.player.event({'event':'end-file','reason':'eof'})
        await asyncio.sleep(0)
        await self.player.load_task
        self.assertEqual(self.player.index,1)
        self.player.event({'event':'end-file','reason':'error'})
        self.assertEqual(self.player.status,'Error')
        self.assertEqual(self.player.index,1)
        self.assertEqual(len(self.player.queue),2)


class CatalogTests(unittest.TestCase):
    def test_metadata(self):
        row = normalize({'videoId':'x','title':'<song>','artists':[{'name':'A'},{'name':'B'}], 'duration':'3:20'})
        self.assertEqual(row['artist'], 'A, B')
        self.assertEqual(row['duration'], 200)
        self.assertEqual(row['title'], '<song>')

    def test_playlist_url(self):
        self.assertEqual(parse_url('https://music.youtube.com/playlist?list=PL123'), ('playlist','PL123'))
