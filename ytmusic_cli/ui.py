import asyncio
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Input, Static, Button, ListView, ListItem, Label, Select
from textual.containers import Horizontal
from .main import ensure_service
from .service import request, states


class MusicApp(App):
    TITLE = 'YouTube Music'
    CSS = '''
    Screen { layout: vertical; }
    #navigation, #transport, #queue-actions { height: 3; }
    Button { min-width: 9; width: auto; margin-right: 1; }
    #results { height: 1fr; border: round $primary; }
    #now { height: 4; padding: 0 1; }
    #status { height: 2; color: $text-muted; padding: 0 1; }
    #search { margin-bottom: 1; }
    #repeat { width: 17; }
    '''
    BINDINGS = [Binding('q', 'quit', 'Close UI'), Binding('space','toggle','Play/pause'),
                Binding('/','search','Search'), Binding('a','enqueue','Add selection'),
                Binding('delete','remove','Remove from queue'), Binding('n','next','Next')]

    def __init__(self):
        super().__init__()
        self.rows = []
        self.view = 'search'
        self.state = {}
        self.subscription = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder='Search songs, or paste a YouTube song/playlist URL', id='search')
        with Horizontal(id='navigation'):
            yield Button('Playlists', id='playlists')
            yield Button('Albums', id='albums')
            yield Button('Liked songs', id='liked')
            yield Button('Queue', id='queue')
            yield Button('Search albums', id='search-albums')
        yield ListView(id='results')
        with Horizontal(id='queue-actions'):
            yield Button('Play all', id='play-all')
            yield Button('Add', id='add')
            yield Button('Remove', id='remove')
            yield Button('Move up', id='up')
            yield Button('Move down', id='down')
            yield Button('Clear queue', id='clear')
        yield Static('No song selected', id='now', markup=False)
        with Horizontal(id='transport'):
            for title, identity in [('Previous','previous'),('Play/pause','toggle'),('Next','next'),('Stop','stop'),('−10s','back'),('+10s','forward'),('Vol−','quieter'),('Vol+','louder'),('Shuffle','shuffle')]:
                yield Button(title, id=identity)
            yield Select([('Repeat off','off'),('Repeat one','one'),('Repeat all','all')], value='off', allow_blank=False, id='repeat')
        yield Static('Connecting…', id='status', markup=False)
        yield Footer()

    async def on_mount(self):
        try:
            await ensure_service()
            self.subscription = self.run_worker(self.watch_state(), exclusive=True, group='subscription')
            self.query_one('#search', Input).focus()
        except Exception as error:
            self.query_one('#status', Static).update(str(error))

    async def watch_state(self):
        while True:
            try:
                async for state in states():
                    previous_queue = self.state.get('queue')
                    self.state = state
                    track = state.get('track', {})
                    position, duration = int(state.get('position') or 0), int(state.get('duration') or 0)
                    self.query_one('#now', Static).update(
                        f"{track.get('title', 'No song selected')}\n{track.get('artist', '')}\n"
                        f"{position//60}:{position%60:02d} / {duration//60}:{duration%60:02d}  ·  {state['status']}  ·  Volume {state['volume']:.0f}%")
                    self.query_one('#status', Static).update(state.get('error') or f"Shuffle {'on' if state['shuffle'] else 'off'} · Repeat {state['repeat']} · Closing this interface keeps music playing")
                    if self.view == 'queue' and previous_queue != state['queue']:
                        await self.show(state['queue'], 'queue')
                await asyncio.sleep(1)
            except (OSError, ValueError, RuntimeError):
                self.query_one('#status', Static).update('Disconnected. Reconnecting…')
                await asyncio.sleep(2)
                try:
                    await ensure_service()
                except Exception:
                    pass

    async def show(self, rows, view):
        widget = self.query_one('#results', ListView)
        selected = widget.index
        await widget.clear()
        self.rows = rows
        self.view = view
        for row in rows:
            text = row.get('title', 'Untitled')
            if row.get('artist'):
                text += ' — ' + row['artist']
            await widget.append(ListItem(Label(text, markup=False)))
        if rows:
            widget.index = min(selected or 0, len(rows)-1)
        widget.focus()

    def selected(self):
        index = self.query_one('#results', ListView).index
        if index is None or index >= len(self.rows):
            raise ValueError('Select a song first')
        return index, self.rows[index]

    async def perform(self, method, params=None):
        try:
            return await request(method, params)
        except Exception as error:
            self.notify(str(error), severity='error', timeout=8)
            return None

    async def on_input_submitted(self, event):
        async def search():
            self.query_one('#status', Static).update('Searching…')
            if event.value.startswith('https://'):
                await self.perform('play', {'url': event.value})
            else:
                rows = await self.perform('search', {'query': event.value, 'filter': 'songs'})
                if rows is not None:
                    await self.show(rows, 'search')
        self.run_worker(search(), group='catalog', exclusive=True)

    async def on_list_view_selected(self, event):
        try:
            index, row = self.selected()
            if row.get('kind') in ('playlist','album'):
                async def open_collection():
                    rows = await self.perform(row['kind'], {'id': row.get('playlistId') or row.get('browseId')})
                    if rows is not None:
                        await self.show(rows, 'collection')
                self.run_worker(open_collection(), group='catalog', exclusive=True)
            elif self.view == 'queue':
                await self.perform('select', {'index': index})
            else:
                await self.perform('play', {'tracks': [row]})
        except ValueError as error:
            self.notify(str(error))

    async def on_select_changed(self, event):
        if event.select.id == 'repeat' and self.state:
            await self.perform('repeat', {'value': event.value})

    async def on_button_pressed(self, event):
        method = event.button.id
        try:
            if method in ('playlists','albums','liked','search-albums'):
                async def browse():
                    if method == 'liked':
                        rows = await self.perform('liked')
                    elif method == 'search-albums':
                        rows = await self.perform('search', {'query': self.query_one('#search', Input).value, 'filter':'albums'})
                    else:
                        rows = await self.perform('library', {'kind': method})
                    if rows is not None:
                        await self.show(rows, 'library')
                self.run_worker(browse(), group='catalog', exclusive=True)
            elif method == 'queue':
                await self.show(self.state.get('queue', []), 'queue')
            elif method == 'play-all':
                await self.perform('play', {'tracks': [r for r in self.rows if r.get('videoId')]})
            elif method == 'add':
                await self.action_enqueue()
            elif method in ('remove','up','down'):
                if self.view != 'queue':
                    raise ValueError('Open Queue to edit it')
                index, _ = self.selected()
                if method == 'remove':
                    await self.perform('remove', {'index':index})
                else:
                    target = index + (-1 if method == 'up' else 1)
                    if 0 <= target < len(self.rows):
                        await self.perform('move', {'index':index, 'target':target})
            elif method == 'toggle':
                await self.action_toggle()
            elif method in ('back','forward'):
                await self.perform('seek', {'value':self.state.get('position',0)+(-10 if method=='back' else 10)})
            elif method in ('quieter','louder'):
                await self.perform('volume', {'value':self.state.get('volume',100)+(-5 if method=='quieter' else 5)})
            else:
                await self.perform(method)
        except ValueError as error:
            self.notify(str(error))

    async def action_toggle(self):
        await self.perform('pause' if self.state.get('status') in ('Playing','Loading') else 'resume')

    def action_search(self):
        self.query_one('#search', Input).focus()

    async def action_enqueue(self):
        try:
            _, row = self.selected()
            if row.get('kind') in ('playlist','album'):
                await self.perform('enqueue', {'kind':row['kind'], 'id':row.get('playlistId') or row.get('browseId')})
            else:
                await self.perform('enqueue', {'tracks':[row]})
        except ValueError as error:
            self.notify(str(error))

    async def action_remove(self):
        if self.view == 'queue':
            try:
                index, _ = self.selected()
                await self.perform('remove', {'index':index})
            except ValueError:
                pass

    async def action_next(self):
        await self.perform('next')
