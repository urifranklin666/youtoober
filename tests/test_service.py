import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(root), XDG_CONFIG_HOME=str(root/'config'),
                   XDG_STATE_HOME=str(root/'state'), XDG_CACHE_HOME=str(root/'cache'))
        self.socket = root/f'ytmusic-cli-{os.getuid()}.sock'
        self.process = subprocess.Popen([sys.executable,'-m','ytmusic_cli.main','service','run'],env=env,
                                        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if self.socket.exists():
                return
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            await asyncio.sleep(.05)
        self.fail('Service did not start')

    async def asyncTearDown(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            await asyncio.to_thread(self.process.wait, 10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            await asyncio.to_thread(self.process.wait)
        self.process.stderr.close()
        self.directory.cleanup()

    async def rpc(self, method, params=None, version=1):
        reader,writer = await asyncio.open_unix_connection(self.socket)
        try:
            writer.write((json.dumps({'version':version,'id':123,'method':method,'params':params or {}})+'\n').encode())
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(),5))
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_clients_events_protocol_and_persistence(self):
        reader,writer = await asyncio.open_unix_connection(self.socket)
        writer.write(b'{"version":1,"method":"subscribe","id":1}\n')
        await writer.drain()
        self.assertIn('result',json.loads(await reader.readline()))
        self.assertEqual(json.loads(await reader.readline())['event'],'state')
        results = await asyncio.gather(self.rpc('enqueue',{'url':'https://music.youtube.com/watch?v=test'}),self.rpc('status'))
        self.assertIn('result',results[0])
        for _ in range(5):
            event=json.loads(await asyncio.wait_for(reader.readline(),2))
            if event['state']['queue']:
                break
        self.assertEqual(len(event['state']['queue']),1)
        video=await self.rpc('video-frame',{'after':-1})
        self.assertEqual(video['result']['frame'],'')
        self.assertNotIn('video', (await self.rpc('status'))['result'])
        invalid=await self.rpc('status',version=999)
        self.assertEqual(invalid['id'],123)
        self.assertIn('error',invalid)
        self.assertEqual(self.socket.stat().st_mode & 0o777,0o600)
        writer.close()
        await writer.wait_closed()
        await self.rpc('shutdown')
        await asyncio.to_thread(self.process.wait,10)
        state=Path(self.directory.name)/'state/ytmusic-cli/session.json'
        self.assertEqual(len(json.loads(state.read_text())['queue']),1)
