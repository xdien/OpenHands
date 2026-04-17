import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
import websockets

logger = logging.getLogger(__name__)


async def websocket_proxy_to_sandbox(websocket: WebSocket) -> None:
    """WebSocket proxy handler - extracts parameters from scope.

    This function is used directly with Starlette's WebSocketRoute,
    so path parameters must be extracted from the scope dict.
    """
    path_params = websocket.scope.get("path_params", {})
    sandbox_port_str = path_params.get("sandbox_port", "")
    conversation_id = path_params.get("conversation_id", "")

    query_params = dict(websocket.query_params)
    session_api_key = query_params.get("session_api_key")
    resend_all = query_params.get("resend_all", "false").lower() == "true"

    try:
        sandbox_port = int(sandbox_port_str)
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f"Port {sandbox_port} out of range")
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid port: {sandbox_port_str} - {e}")
        await websocket.close(code=1008, reason="Invalid port")
        return

    logger.info(f"WebSocket PROXY: /ws/{sandbox_port}/sockets/events/{conversation_id}")

    try:
        await websocket.accept()
        logger.info(f"WebSocket accepted, port={sandbox_port}")
    except Exception as e:
        logger.error(f"WebSocket accept failed: {e}")
        return

    sandbox_ws_url = f"ws://localhost:{sandbox_port}/sockets/events/{conversation_id}"

    params = []
    if session_api_key:
        params.append(f"session_api_key={session_api_key}")
    if resend_all:
        params.append("resend_all=true")

    if params:
        sandbox_ws_url += "?" + "&".join(params)

    sandbox_ws = None

    try:
        sandbox_ws = await websockets.connect(sandbox_ws_url)
        logger.info(f"Connected to sandbox WebSocket: port={sandbox_port}")

        async def relay_frontend_to_sandbox():
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.receive":
                        if "text" in message:
                            await sandbox_ws.send(message["text"])
                        elif "bytes" in message:
                            await sandbox_ws.send(message["bytes"])
                    elif message["type"] == "websocket.disconnect":
                        break
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.error(f"Error relaying frontend to sandbox: {e}")
            finally:
                if sandbox_ws:
                    try:
                        await sandbox_ws.close()
                    except Exception:
                        pass

        async def relay_sandbox_to_frontend():
            try:
                while True:
                    message = await sandbox_ws.recv()
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    elif isinstance(message, bytes):
                        await websocket.send_bytes(message)
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception as e:
                logger.error(f"Error relaying sandbox to frontend: {e}")
            finally:
                try:
                    await websocket.close(code=1000, reason="Sandbox disconnected")
                except Exception:
                    pass

        await asyncio.gather(
            relay_frontend_to_sandbox(),
            relay_sandbox_to_frontend(),
            return_exceptions=True,
        )

    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        await websocket.close(code=1011, reason=str(e))

    finally:
        if sandbox_ws:
            try:
                await sandbox_ws.close()
            except Exception:
                pass
        logger.info(f"WebSocket proxy closed: port={sandbox_port}")
