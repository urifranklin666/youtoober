"""YouTube Music catalog access; no account secrets enter public track objects."""
import asyncio
import hashlib
import json
from urllib.parse import urlsplit, parse_qs
from urllib.request import urlopen, Request
from .paths import CONFIG, CACHE, private_dir


def duration_seconds(value):
    if isinstance(value, (int, float)):
        return max(0, value)
    try:
        result = 0
        for part in str(value or '0').split(':'):
            result = result * 60 + int(part)
        return result
    except ValueError:
        return 0


def normalize(row, kind='song'):
    artists = row.get('artists') or []
    thumbnails = row.get('thumbnails') or []
    album = row.get('album') or {}
    video = row.get('videoId') or ''
    return {'kind': kind, 'videoId': video, 'title': str(row.get('title') or 'Untitled'),
            'artist': ', '.join(str(a.get('name', '')) for a in artists),
            'album': album.get('name', '') if isinstance(album, dict) else str(album),
            'duration': duration_seconds(row.get('duration_seconds') or row.get('duration')),
            'thumbnail': thumbnails[-1].get('url', '') if thumbnails else '',
            'artwork': '', 'browseId': str(row.get('browseId') or ''),
            'playlistId': str(row.get('playlistId') or ''),
            'url': f'https://music.youtube.com/watch?v={video}' if video else ''}


def parse_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('youtube.com', 'www.youtube.com', 'music.youtube.com', 'youtu.be'):
        raise ValueError('Provide an HTTPS YouTube song or playlist URL')
    query = parse_qs(parsed.query)
    video = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else (query.get('v') or [''])[0]
    playlist = (query.get('list') or [''])[0]
    if video:
        return 'song', video
    if playlist:
        return 'playlist', playlist
    raise ValueError('The URL has no song or playlist ID')


class Catalog:
    def api(self, authenticated=False):
        from ytmusicapi import YTMusic
        auth = CONFIG / 'browser.json'
        if authenticated and not auth.exists():
            raise RuntimeError('Run ytm auth to import your signed-in YouTube Music session')
        import requests
        from functools import partial
        session = requests.Session()
        session.request = partial(session.request, timeout=20)
        return YTMusic(str(auth) if auth.exists() else None, requests_session=session)

    async def call(self, method, params):
        def fetch():
            search_filter = params.get('filter', 'songs')
            if method == 'search' and search_filter not in ('songs', 'albums', 'videos'):
                raise ValueError('Search filter must be songs, albums, or videos')
            authenticated = method in ('library', 'liked')
            api = self.api(authenticated)
            if method == 'search':
                result = api.search(params['query'], filter=search_filter, limit=50)
                fallback = {'albums': 'album', 'videos': 'video'}.get(search_filter, 'song')
                kinds = {'album': 'album', 'video': 'video', 'song': 'song'}
                return [normalize(r, kinds.get(r.get('resultType'), fallback)) for r in result]
            if method == 'library':
                kind = params.get('kind', 'playlists')
                if kind == 'albums':
                    return [normalize(r, 'album') for r in api.get_library_albums(limit=None)]
                if kind == 'songs':
                    return [normalize(r) for r in api.get_library_songs(limit=None)]
                return [normalize(r, 'playlist') for r in api.get_library_playlists(limit=None)]
            if method == 'liked':
                return [normalize(r) for r in api.get_liked_songs(limit=None)['tracks'] if r.get('videoId')]
            if method == 'playlist':
                return [normalize(r) for r in api.get_playlist(params['id'], limit=None)['tracks'] if r.get('videoId')]
            if method == 'album':
                album = api.get_album(params['id'])
                result = []
                for row in album['tracks']:
                    row = dict(row, album={'name': album.get('title', '')}, thumbnails=album.get('thumbnails', []))
                    if row.get('videoId'):
                        result.append(normalize(row))
                return result
            raise ValueError('Unknown catalog command')
        try:
            return await asyncio.to_thread(fetch)
        except (ValueError, RuntimeError):
            raise
        except Exception:
            raise RuntimeError('YouTube Music request failed. Check your connection; run ytm auth if your session expired.') from None

    async def tracks(self, params):
        if params.get('tracks'):
            # Whitelist fields rather than accepting paths or arbitrary player options.
            result = []
            for row in params['tracks']:
                kind, video = parse_url(row.get('url') or f"https://music.youtube.com/watch?v={row.get('videoId', '')}")
                if kind != 'song':
                    raise ValueError('Expected songs')
                track = normalize({'videoId': video, 'title': row.get('title', video),
                                   'artists': [{'name': row.get('artist', '')}],
                                   'album': row.get('album', ''), 'duration': row.get('duration', 0),
                                   'thumbnails': [{'url': row.get('thumbnail', '')}]},
                                  kind='video' if row.get('kind') == 'video' else 'song')
                result.append(track)
            return result
        if params.get('kind') in ('playlist', 'album'):
            return await self.call(params['kind'], {'id': params['id']})
        kind, identity = parse_url(params['url'])
        if kind == 'playlist':
            return await self.call('playlist', {'id': identity})
        return [normalize({'videoId': identity, 'title': identity})]

    async def artwork(self, track):
        url = track.get('thumbnail', '')
        if not url.startswith('https://'):
            return ''
        path = CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.img')
        def fetch():
            private_dir(CACHE)
            if not path.exists():
                with urlopen(Request(url, headers={'User-Agent': 'ytmusic-cli/0.2'}), timeout=10) as response:
                    body = response.read(5_000_001)
                if len(body) > 5_000_000:
                    return ''
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(body)
                temporary.replace(path)
            return str(path)
        try:
            return await asyncio.to_thread(fetch)
        except Exception:
            return ''
