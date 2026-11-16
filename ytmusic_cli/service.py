import asyncio
import fcntl
import json
import os
import signal
from .paths import SOCKET, RUNTIME, private_dir
from .player import Player


async def request(method, params=None, timeout=60):
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET, limit=8_000_000), 3)
    try:
        writer.write((json.dumps({'version': 1, 'id': 1, 'method': method, 'params': params or {}})+'\n').encode())
        await writer.drain()
        data = json.loads(await asyncio.wait_for(reader.readline(), timeout))
        if 'error' in data:
            raise RuntimeError(data['error']['message'])
        return data['result']
    finally:
        writer.close()
        await writer.wait_closed()


async def states():
    reader, writer = await asyncio.open_unix_connection(SOCKET, limit=8_000_000)
    writer.write(b'{"version":1,"id":1,"method":"subscribe"}\n')
    await writer.drain()
    try:
        while line := await reader.readline():
            message = json.loads(line)
            if message.get('event') == 'state':
                yield message['state']
        raise ConnectionError('Player disconnected')
    finally:
        writer.close()
        await writer.wait_closed()


async def serve():
    os.umask(0o077)
    private_dir(RUNTIME)
    lock = (SOCKET.with_suffix('.lock')).open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Player service already running') from None
    SOCKET.unlink(missing_ok=True)
    player = Player()
    subscribers = set()
    clients = set()
    stop = asyncio.Event()
    mutation = asyncio.Lock()
    catalog_slots = asyncio.Semaphore(4)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async def client(reader, writer):
        identity = None
        clients.add(writer)
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            data = json.loads(line)
            identity = data.get('id')
            if data.get('version') != 1:
                raise ValueError('Unsupported protocol version')
            method, params = data['method'], data.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            if method == 'subscribe':
                writer.write((json.dumps({'id': identity, 'result': {'version': 1}})+'\n').encode())
                writer.write((json.dumps({'event': 'state', 'sequence': 0, 'state': player.snapshot()})+'\n').encode())
                await writer.drain()
                subscribers.add(writer)
                try:
                    await reader.read()
                finally:
                    subscribers.discard(writer)
                    clients.discard(writer)
                    writer.close()
                return
            if method == 'shutdown':
                stop.set()
                result = True
            elif method in ('status', 'queue'):
                result = await player.dispatch(method, params)
            elif method in ('search', 'library', 'liked', 'playlist', 'album'):
                async with catalog_slots:
                    result = await player.dispatch(method, params)
            elif method in ('play', 'enqueue'):
                async with catalog_slots:
                    tracks = await player.catalog.tracks(params)
                async with mutation:
                    result = await player.dispatch(method, {'tracks': tracks})
            else:
                async with mutation:
                    result = await player.dispatch(method, params)
            response = {'id': identity, 'result': result}
        except Exception as error:
            response = {'id': identity, 'error': {'code': 'request_failed', 'message': str(error)}}
        finally:
            subscribers.discard(writer)
        try:
            if 'response' in locals():
                writer.write((json.dumps(response)+'\n').encode())
                await writer.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            clients.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def publish():
        sequence = 0
        while True:
            await asyncio.sleep(.25)
            sequence += 1
            state = player.snapshot()
            body = (json.dumps({'event': 'state', 'sequence': sequence, 'state': state})+'\n').encode()
            for writer in list(subscribers):
                try:
                    writer.write(body)
                    await asyncio.wait_for(writer.drain(), 1)
                except (OSError, TimeoutError):
                    subscribers.discard(writer)
                    clients.discard(writer)
                    writer.close()
            if sequence % 20 == 0:
                player.persist()
    publisher = None
    server = None
    try:
        await player.backend.start()
        server = await asyncio.start_unix_server(client, SOCKET, limit=8_000_000)
        SOCKET.chmod(0o600)
        publisher = asyncio.create_task(publish())
        async with server:
            await stop.wait()
            for writer in list(clients):
                writer.close()
    finally:
        if publisher:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
        for writer in list(subscribers):
            writer.close()
        await player.close()
        SOCKET.unlink(missing_ok=True)
        lock.close()
