import asyncio
import logging
import os
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def get_sandbox_url_for_port(sandbox_port: int) -> str | None:
    """Look up the sandbox URL from the database based on port.

    Returns the internal_url from the sandbox's exposed_urls if found.
    Currently returns None to use fallback hosts - can be enhanced later
    to query the database when proper dependency injection is available.
    """
    return None


async def websocket_proxy_to_sandbox(websocket: WebSocket) -> None:
    """WebSocket proxy handler - extracts parameters from scope.

    This function is used directly with Starlette's WebSocketRoute,
    so path parameters must be extracted from the scope dict.
    """
    path_params = websocket.scope.get('path_params', {})
    sandbox_port_str = path_params.get('sandbox_port', '')
    conversation_id = path_params.get('conversation_id', '')

    query_params = dict(websocket.query_params)
    session_api_key = query_params.get('session_api_key')
    resend_all = query_params.get('resend_all', 'false').lower() == 'true'

    try:
        sandbox_port = int(sandbox_port_str)
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port: {sandbox_port_str} - {e}')
        await websocket.close(code=1008, reason='Invalid port')
        return

    logger.info(f'WebSocket PROXY: /ws/{sandbox_port}/sockets/events/{conversation_id}')

    # Try to get the actual sandbox URL from the database
    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    # Fallback: try different hosts
    hosts_to_try: list[str] = []
    if sandbox_base_url:
        # Extract host from database URL
        try:
            if '://' in sandbox_base_url:
                hosts_to_try.append(sandbox_base_url.split('://')[1].split('/')[0])
        except Exception:
            pass

    # Add fallback hosts for Docker/remote scenarios
    hosts_to_try.extend(
        [
            f'localhost:{sandbox_port}',
            f'127.0.0.1:{sandbox_port}',
        ]
    )

    # In Docker, try host.docker.internal
    if os.path.exists('/.dockerenv'):
        hosts_to_try.append(f'host.docker.internal:{sandbox_port}')

    # Deduplicate
    hosts_to_try = list(dict.fromkeys(hosts_to_try))

    sandbox_ws: Any = None
    connected = False

    for host in hosts_to_try:
        if connected:
            break
        sandbox_ws_url = f'ws://{host}/sockets/events/{conversation_id}'

        params = []
        if session_api_key:
            params.append(f'session_api_key={session_api_key}')
        if resend_all:
            params.append('resend_all=true')
        if params:
            sandbox_ws_url += '?' + '&'.join(params)

        logger.info(f'Trying WebSocket connection to: {sandbox_ws_url.split("?")[0]}')

        try:
            sandbox_ws = await websockets.connect(sandbox_ws_url, open_timeout=5)
            logger.info(f'Connected to sandbox WebSocket: {host}')
            connected = True
        except Exception as e:
            logger.warning(f'Failed to connect to {host}: {e}')
            continue

    if not connected:
        logger.error(
            f'Could not connect to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
        )
        await websocket.close(code=1011, reason='Sandbox not available')
        return

    try:
        await websocket.accept()
        logger.info(f'WebSocket accepted, port={sandbox_port}')
    except Exception as e:
        logger.error(f'WebSocket accept failed: {e}')
        if sandbox_ws:
            await sandbox_ws.close()
        return

    async def relay_frontend_to_sandbox() -> None:
        ws = sandbox_ws
        if not ws:
            return
        try:
            while True:
                message = await websocket.receive()
                if message['type'] == 'websocket.receive':
                    if 'text' in message:
                        await ws.send(message['text'])
                    elif 'bytes' in message:
                        await ws.send(message['bytes'])
                elif message['type'] == 'websocket.disconnect':
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f'Error relaying frontend to sandbox: {e}')
        finally:
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def relay_sandbox_to_frontend() -> None:
        ws = sandbox_ws
        if not ws:
            return
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    await websocket.send_text(message)
                elif isinstance(message, bytes):
                    await websocket.send_bytes(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f'Error relaying sandbox to frontend: {e}')
        finally:
            try:
                await websocket.close(code=1000, reason='Sandbox disconnected')
            except Exception:
                pass

    try:
        await asyncio.gather(
            relay_frontend_to_sandbox(),
            relay_sandbox_to_frontend(),
            return_exceptions=True,
        )
    finally:
        if sandbox_ws:
            try:
                await sandbox_ws.close()
            except Exception:
                pass
        logger.info(f'WebSocket proxy closed: port={sandbox_port}')
