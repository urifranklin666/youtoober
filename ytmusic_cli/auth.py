"""Validate local header imports without logging credential values."""
import json
from ytmusicapi import setup


def parse_headers(raw):
    try:
        text = raw.strip()
        if text.startswith('fetch('):
            options, _ = json.JSONDecoder().raw_decode(text[text.index('{'):])
            if options.get('method', '').upper() != 'POST':
                raise ValueError('Expected a POST request')
            headers = options['headers']
            text = '\n'.join(f'{key}: {value}' for key, value in headers.items())
        elif text.startswith('{'):
            headers = json.loads(text)
            text = '\n'.join(f'{key}: {value}' for key, value in headers.items())
        parsed = json.loads(setup(headers_raw=text))
        if not all(parsed.get(key) for key in ('authorization', 'cookie', 'x-goog-authuser')):
            raise ValueError('Missing authentication headers')
        return parsed
    except Exception:
        raise ValueError('Invalid authentication headers. Copy the signed-in POST /youtubei/v1/browse request, including authorization, cookie, and x-goog-authuser; or use ytm auth --headers-file ~/headers.txt.') from None
