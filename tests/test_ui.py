import unittest
from unittest.mock import AsyncMock, patch
from ytmusic_cli.ui import MusicApp
from textual.widgets import Input, ListView


async def no_states():
    if False:
        yield {}


class UITests(unittest.IsolatedAsyncioTestCase):
    async def test_search_collection_and_queue_controls(self):
        rows=[{'title':'Literal <song>', 'artist':'Artist', 'kind':'song','videoId':'abc','url':'https://music.youtube.com/watch?v=abc'}]
        async def request(method, params=None):
            if method == 'search':
                return rows
            return {}
        with patch('ytmusic_cli.ui.ensure_service',new=AsyncMock()), patch('ytmusic_cli.ui.states',no_states), patch('ytmusic_cli.ui.request',side_effect=request) as rpc:
            app = MusicApp()
            async with app.run_test(size=(140,40)) as pilot:
                field=app.query_one('#search',Input)
                field.value='Artist'
                await pilot.press('enter')
                await pilot.pause()
                self.assertEqual(len(app.rows),1)
                self.assertEqual(app.rows[0]['title'],'Literal <song>')
                app.query_one(ListView).index=0
                await app.action_enqueue()
                self.assertEqual(rpc.call_args.args[0],'enqueue')
                await app.show(rows,'queue')
                await app.action_remove()
                self.assertEqual(rpc.call_args.args[0],'remove')

    async def test_video_mode_persists_and_results_play_or_queue(self):
        from textual.widgets import Button
        video={'kind':'video','title':'Official music video','artist':'Demo artist',
               'videoId':'exact-video','url':'https://music.youtube.com/watch?v=exact-video'}
        async def request(method, params=None):
            return [video] if method=='search' else {}
        with patch('ytmusic_cli.ui.ensure_service',new=AsyncMock()), patch('ytmusic_cli.ui.states',no_states), patch('ytmusic_cli.ui.request',side_effect=request) as rpc:
            app=MusicApp()
            async with app.run_test(size=(140,40)) as pilot:
                await pilot.click('#search-videos')
                await pilot.pause()
                rpc.assert_not_called()
                self.assertEqual(app.query_one('#search-videos',Button).variant,'primary')
                field=app.query_one('#search',Input)
                field.value='artist official'; field.focus()
                await pilot.press('enter'); await pilot.pause()
                self.assertEqual(rpc.call_args.args,('search',{'query':'artist official','filter':'videos'}))
                self.assertEqual(app.rows[0]['kind'],'video')
                app.query_one(ListView).index=0
                app.query_one(ListView).focus()
                await pilot.press('enter'); await pilot.pause()
                self.assertEqual(rpc.call_args.args,('play',{'tracks':[video]}))
                await app.action_enqueue()
                self.assertEqual(rpc.call_args.args,('enqueue',{'tracks':[video]}))
                field.value='another video'; field.focus()
                await pilot.press('enter'); await pilot.pause()
                self.assertEqual(rpc.call_args.args,('search',{'query':'another video','filter':'videos'}))
                await pilot.click('#search-songs'); await pilot.pause()
                self.assertEqual(rpc.call_args.args,('search',{'query':'another video','filter':'songs'}))
