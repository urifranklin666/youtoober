import unittest
from unittest.mock import Mock, patch
from ytmusic_cli.catalog import Catalog
from ytmusic_cli.main import build_parser


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_search_and_queue_preserve_exact_upload(self):
        api=Mock()
        api.search.return_value=[{'resultType':'video','videoId':'exact-upload','title':'Official video',
                                  'artists':[{'name':'Demo artist'}],'duration':'4:12',
                                  'thumbnails':[{'url':'https://example.test/cover.jpg'}]}]
        catalog=Catalog()
        with patch.object(catalog,'api',return_value=api):
            rows=await catalog.call('search',{'query':'artist official','filter':'videos'})
        api.search.assert_called_once_with('artist official',filter='videos',limit=50)
        self.assertEqual(rows[0]['kind'],'video')
        self.assertEqual(rows[0]['duration'],252)
        queued=await catalog.tracks({'tracks':rows})
        self.assertEqual(queued[0]['videoId'],'exact-upload')
        self.assertEqual(queued[0]['url'],'https://music.youtube.com/watch?v=exact-upload')
        self.assertEqual(queued[0]['kind'],'video')
        self.assertEqual(queued[0]['artist'],'Demo artist')

    async def test_filter_fallback_and_empty_results(self):
        catalog=Catalog(); api=Mock()
        with patch.object(catalog,'api',return_value=api):
            for filter,kind in [('songs','song'),('albums','album'),('videos','video')]:
                api.search.return_value=[{'title':'Demo','videoId':'demo'}]
                rows=await catalog.call('search',{'query':'demo','filter':filter})
                self.assertEqual(rows[0]['kind'],kind)
            api.search.return_value=[]
            self.assertEqual(await catalog.call('search',{'query':'nothing','filter':'videos'}),[])

    async def test_unknown_filter_rejected_without_catalog_access(self):
        catalog=Catalog()
        with patch.object(catalog,'api') as api:
            with self.assertRaisesRegex(ValueError,'Search filter'):
                await catalog.call('search',{'query':'demo','filter':'invalid'})
            api.assert_not_called()

    def test_cli_video_filter_and_song_default(self):
        parser=build_parser()
        args=parser.parse_args(['search','--filter','videos','official','video'])
        self.assertEqual(args.filter,'videos')
        self.assertEqual(args.query,['official','video'])
        self.assertEqual(parser.parse_args(['search','demo']).filter,'songs')
