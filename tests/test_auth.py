import json
import unittest
from ytmusic_cli.auth import parse_headers


class AuthTests(unittest.TestCase):
    headers = {'authorization':'SAPISIDHASH test', 'cookie':'SAPISID=fake', 'x-goog-authuser':'0'}

    def test_raw_headers(self):
        parsed=parse_headers('\n'.join(f'{k}: {v}' for k,v in self.headers.items()))
        self.assertEqual(parsed['cookie'],'SAPISID=fake')

    def test_post_fetch_and_json(self):
        raw='fetch("https://music.youtube.com/youtubei/v1/browse", '+json.dumps({'method':'POST','headers':self.headers})+');'
        self.assertEqual(parse_headers(raw)['authorization'],'SAPISIDHASH test')
        self.assertEqual(parse_headers(json.dumps(self.headers))['x-goog-authuser'],'0')

    def test_head_or_missing_headers_raise_safe_error(self):
        for raw in ('fetch("https://music.youtube.com/", '+json.dumps({'method':'HEAD','headers':self.headers})+');', 'cookie: private_cookie_value'):
            with self.assertRaises(ValueError) as error:
                parse_headers(raw)
            self.assertNotIn('private_cookie_value',str(error.exception))
            self.assertNotIn('SAPISID=fake',str(error.exception))
