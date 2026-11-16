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
