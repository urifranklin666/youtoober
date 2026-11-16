#!/usr/bin/env python3
"""Capture the real Textual interface with synthetic data and no service access."""
import asyncio
import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, patch
# The capture runner is not an interactive terminal; render the application's normal colors.
os.environ.pop('NO_COLOR', None)
os.environ['TERM'] = 'xterm-256color'
os.environ['COLORTERM'] = 'truecolor'
from ytmusic_cli.ui import MusicApp

ROOT = Path(__file__).resolve().parents[1]


def sample_queue():
    return [dict(id=f'demo-{i}', title=title, artist=artist, kind='song', duration=duration)
            for i, (title, artist, duration) in enumerate([
                ('Midnight in Motion', 'The Night Session', 240),
                ('Blue Hour', 'Neon Atlas', 216),
                ('Parallel Lines', 'Northbound', 198),
                ('Afterglow', 'The Night Session', 263),
                ('Homeward', 'Sunday Signals', 224),
                ('Quiet Satellites', 'Atlas Coast', 251),
            ])]


async def capture():
    queue = sample_queue()
    state = dict(queue=queue, track=queue[0], index=0, status='Playing', position=87,
                 duration=240, volume=65, shuffle=False, repeat='off', error='')

    async def demo_states():
        yield state
        await asyncio.Event().wait()

    async def demo_request(method, params=None):
        return queue if method == 'queue' else state

    with patch('ytmusic_cli.ui.ensure_service', new=AsyncMock()), \
         patch('ytmusic_cli.ui.states', demo_states), \
         patch('ytmusic_cli.ui.request', demo_request):
        app = MusicApp()
        async with app.run_test(size=(140, 32)) as pilot:
            await pilot.pause()
            await app.show(queue, 'queue')
            await pilot.pause()
            svg = ROOT / 'build/terminal-interface.svg'
            svg.parent.mkdir(exist_ok=True)
            svg.write_text(app.export_screenshot(title='YouToober — Terminal player'))
    output = ROOT / 'docs/screenshots/terminal-interface.png'
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rsvg-convert', '--format', 'png', '--output', str(output), str(svg)], check=True)
    print(f'Captured {output.relative_to(ROOT)} using synthetic data; no player service was contacted.')


if __name__ == '__main__':
    asyncio.run(capture())
