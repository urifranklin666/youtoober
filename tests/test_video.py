import asyncio
import base64
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
from ytmusic_cli.video import Video
from ytmusic_cli.backend import Backend


class VideoTests(unittest.IsolatedAsyncioTestCase):
    def player(self):
        return SimpleNamespace(track={'id':'one','url':'https://music.youtube.com/watch?v=one'},status='Playing',position=0)

    async def test_snapshot_never_exposes_stream_or_headers(self):
        player=self.player()
        video=Video(player)
        video.key='one'; video.jpeg=b'jpeg'; video.status='ready'; video.revision=7; video.frame_at=time.monotonic()
        video.cached={'url':'https://private.invalid/signed', 'http_headers':{'Cookie':'private-cookie'}}
        snapshot=video.snapshot()
        self.assertEqual(base64.b64decode(snapshot['frame']),b'jpeg')
        self.assertEqual(video.snapshot(after=7)['frame'],'')
        self.assertNotIn('private',str(snapshot))
        player.track={'id':'two'}
        self.assertEqual(video.snapshot()['frame'],'')
        self.assertEqual(video.snapshot()['status'],'loading')

    async def test_stalled_stream_falls_back_but_paused_frame_is_retained(self):
        player=self.player(); video=Video(player)
        video.key='one'; video.jpeg=b'jpeg'; video.status='ready'; video.frame_at=time.monotonic()-3
        self.assertEqual(video.snapshot()['status'],'loading')
        self.assertEqual(video.snapshot()['frame'],'')
        player.status='Paused'
        self.assertEqual(video.snapshot()['status'],'ready')
        self.assertTrue(video.snapshot()['frame'])

    async def test_no_demand_means_no_extraction(self):
        resolver=SimpleNamespace(resolve=AsyncMock(),extractor=None)
        video=Video(self.player(),resolver)
        monitor=asyncio.create_task(video.run())
        await asyncio.sleep(.25)
        monitor.cancel(); await asyncio.gather(monitor,return_exceptions=True)
        resolver.resolve.assert_not_awaited()
        await video.close()

    async def test_failures_fall_back_without_retry_storm(self):
        resolver=SimpleNamespace(resolve=AsyncMock(side_effect=RuntimeError('secret diagnostic')),extractor=None)
        video=Video(self.player(),resolver)
        video.snapshot()
        monitor=asyncio.create_task(video.run())
        try:
            await asyncio.sleep(.45)
            self.assertEqual(video.snapshot()['status'],'unavailable')
            self.assertNotIn('secret',str(video.snapshot()))
            self.assertEqual(resolver.resolve.await_count,1)
        finally:
            monitor.cancel(); await asyncio.gather(monitor,return_exceptions=True); await video.close()

    async def test_decoder_refreshes_once_then_falls_back(self):
        resolver=SimpleNamespace(resolve=AsyncMock(return_value={'url':'https://example.invalid/video','height':720,'vcodec':'h264'}),extractor=None)
        video=Video(self.player(),resolver)
        video.decode=AsyncMock(return_value=False)
        await video.play(video.player.track,0)
        self.assertEqual(resolver.resolve.await_count,2)
        self.assertEqual(video.decode.await_count,2)
        self.assertTrue(video.failed)
        self.assertEqual(video.status,'unavailable')

    async def test_real_silent_decoder_pause_seek_track_change_and_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            sample=Path(folder)/'sample.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=160x90:rate=12','-t','3','-an','-c:v','mpeg4',str(sample)],check=True)
            resolver=SimpleNamespace(resolve=AsyncMock(return_value={'url':str(sample),'height':90,'vcodec':'mpeg4'}),extractor=None)
            player=self.player(); video=Video(player,resolver)
            monitor=asyncio.create_task(video.run())
            async def ready():
                for _ in range(100):
                    snapshot=video.snapshot()
                    if snapshot['status']=='ready' and snapshot['frame']:
                        return snapshot
                    await asyncio.sleep(.03)
                self.fail('Video decoder did not produce a frame')
            try:
                first=await ready()
                self.assertTrue(base64.b64decode(first['frame']).startswith(b'\xff\xd8'))
                player.status='Paused'
                revision=video.revision
                await asyncio.sleep(.15)
                self.assertEqual(video.revision,revision)
                player.position=1.5
                await asyncio.sleep(.15)
                sought=await ready()
                self.assertNotEqual(first['frame'],sought['frame'])
                self.assertEqual(resolver.resolve.await_count,1,'Seeking should reuse the private stream URL')
                player.track={'id':'two','url':'https://music.youtube.com/watch?v=two'}
                changed=await ready()
                self.assertEqual(changed['track_id'],'two')
                self.assertEqual(resolver.resolve.await_count,2)
                video.demand=time.monotonic()-5
                await asyncio.sleep(.15)
                self.assertIsNone(video.process)
                self.assertEqual(video.status,'idle')
            finally:
                monitor.cancel(); await asyncio.gather(monitor,return_exceptions=True); await video.close()

    async def test_video_resolver_caps_quality_and_requires_video(self):
        import json
        class Process:
            returncode=0
            async def communicate(self):
                return json.dumps({'url':'https://example.invalid/video','height':720,'vcodec':'h264'}).encode(),b''
        with tempfile.TemporaryDirectory() as folder, patch('ytmusic_cli.backend.CONFIG',Path(folder)), patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=Process())) as spawn:
            await Backend(lambda _:None).resolve({'url':'https://music.youtube.com/watch?v=one'},video=True)
            args=spawn.call_args.args
            format=args[args.index('-f')+1]
            self.assertIn('height<=720',format)
            self.assertIn('vcodec!=none',format)
            self.assertTrue(format.startswith('bestvideo[height<=720][protocol*=m3u8]'))

    async def test_frames_follow_real_mpv_audio_clock(self):
        from ytmusic_cli.player import Player
        from ytmusic_cli.catalog import Catalog
        class LocalCatalog(Catalog):
            async def artwork(self,track): return ''
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sample=root/'av.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=160x90:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','5','-c:v','mpeg4','-c:a','aac',str(sample)],check=True)
            class LocalBackend(Backend):
                async def resolve(self,track,video=False):
                    return {'url':str(sample),'height':90,'vcodec':'mpeg4','title':'Local fixture','duration':5}
            player=Player(LocalCatalog(),LocalBackend,root/'session.json')
            video=Video(player,LocalBackend(lambda _:None))
            monitor=None
            with patch('ytmusic_cli.backend.SOCKET',root/'player.sock'):
                async def synced(expected=None):
                    for _ in range(150):
                        response=video.snapshot()
                        if response['status']=='ready' and abs(video.frame_position-player.position)<.12 and (expected is None or abs(video.frame_position-expected)<.3):
                            return
                        await asyncio.sleep(.02)
                    self.fail(f'Decoded frames did not synchronize: audio={player.position:.3f}, video={video.frame_position:.3f}, status={player.status}, decoder={video.status}, failed={video.failed}, expected={expected}')
                try:
                    await player.backend.start(audio_output='null')
                    await player.dispatch('play',{'url':'https://music.youtube.com/watch?v=fixture'})
                    await player.load_task
                    monitor=asyncio.create_task(video.run())
                    await synced()
                    await player.dispatch('pause',{})
                    await asyncio.sleep(.1); revision=video.revision
                    await asyncio.sleep(.2)
                    self.assertEqual(video.revision,revision)
                    await player.dispatch('seek',{'value':2})
                    await synced(expected=2)
                    # mpv's paused position may include audio-output latency. The tighter
                    # .12-second audio/video alignment check above remains unchanged.
                    self.assertAlmostEqual(video.frame_position,2,delta=.3)
                    paused_position=player.position
                    await player.dispatch('resume',{})
                    await asyncio.sleep(.2)
                    await synced()
                    self.assertGreater(player.position,paused_position+.05)
                finally:
                    if monitor:
                        monitor.cancel(); await asyncio.gather(monitor,return_exceptions=True)
                    await video.close(); await player.close()
