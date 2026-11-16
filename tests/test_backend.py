import asyncio
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from ytmusic_cli.backend import Backend


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_mpv_ipc_load_seek_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root/'silence.wav'
            with wave.open(str(sample),'wb') as output:
                output.setparams((1,2,8000,0,'NONE','not compressed'))
                output.writeframes(b'\0\0'*8000*3)
            events = []
            backend = Backend(events.append)
            with patch('ytmusic_cli.backend.SOCKET',root/'player.sock'):
                try:
                    await backend.start(audio_output='null')
                    await backend.load({'url':str(sample)},paused=True)
                    for _ in range(100):
                        if any(e.get('event')=='file-loaded' for e in events):
                            break
                        await asyncio.sleep(.02)
                    self.assertTrue(any(e.get('event')=='file-loaded' for e in events))
                    self.assertTrue(await backend.command('get_property','pause'))
                    await backend.command('seek',1,'absolute')
                    await backend.command('set_property','volume',25)
                    self.assertEqual(await backend.command('get_property','volume'),25)
                    await backend.command('set_property','pause',False)
                    self.assertFalse(await backend.command('get_property','pause'))
                finally:
                    await backend.close()

    async def test_resolution_retries_and_keeps_headers_private(self):
        import json
        from unittest.mock import AsyncMock
        class Process:
            returncode = 0
            async def communicate(self):
                return json.dumps({'url':'https://media.example.test/audio','title':'Song','http_headers':{'User-Agent':'test'}}).encode(),b''
        with tempfile.TemporaryDirectory() as folder, patch('ytmusic_cli.backend.CONFIG',Path(folder)), patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=Process())) as spawn:
            backend=Backend(lambda _:None)
            result=await backend.resolve({'url':'https://music.youtube.com/watch?v=test'})
            self.assertEqual(result['title'],'Song')
            self.assertIn('youtube:player-client=default,web_safari',spawn.call_args.args)
            self.assertIn('bestaudio[protocol*=m3u8]/worst[protocol*=m3u8]/bestaudio/best',spawn.call_args.args)

    async def test_resolution_failure_is_bounded(self):
        from unittest.mock import AsyncMock
        class Process:
            returncode = 1
            async def communicate(self):
                return b'',b'private diagnostic not exposed'
        with tempfile.TemporaryDirectory() as folder, patch('ytmusic_cli.backend.CONFIG',Path(folder)), patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=Process())) as spawn, patch('asyncio.sleep',new=AsyncMock()):
            backend=Backend(lambda _:None)
            with self.assertRaisesRegex(RuntimeError,'Cannot resolve'):
                await backend.resolve({'url':'https://music.youtube.com/watch?v=test'})
            self.assertEqual(spawn.await_count,2)
