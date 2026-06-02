# WebSocket Proxy Solution for Sandbox Connections

## Problem

Frontend WebSocket connections to sandbox fail because:
- Nginx cannot proxy `localhost:{random_port}` (sandbox on different machine)
- WebSocket path `/ws/{port}/sockets/events/` hits main app server → closed

## Architecture

Current (Failed):
```
Frontend → wss://domain.com/ws/59219/sockets/events/{id}
Nginx → Cannot proxy to localhost:59219 (different machine)
Main App → SPAStaticFiles closes WebSocket
Result: Connection failed ❌
```

## Solution: App Server WebSocket Proxy

Create a WebSocket proxy route in main app server that:
1. Receives WebSocket connections on `/ws/{port}/sockets/events/{id}`
2. Extracts port and conversation_id from path
3. Establishes WebSocket connection to sandbox at that port
4. Relays messages bidirectionally between frontend and sandbox

### Implementation

#### 1. Create WebSocket Proxy Route

File: `openhands/app_server/websocket_proxy/websocket_proxy_router.py`

```python
import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Path
import httpx

from openhands.app_server.sandbox.sandbox_models import AGENT_SERVER

router = APIRouter()
_logger = logging.getLogger(__name__)

@router.websocket("/ws/{sandbox_port}/sockets/events/{conversation_id}")
async def websocket_proxy(
    websocket: WebSocket,
    sandbox_port: Annotated[int, Path(description="Sandbox port number")],
    conversation_id: Annotated[str, Path(description="Conversation ID")],
    session_api_key: str | None = None,
    resend_all: bool = False,
):
    """
    WebSocket proxy that relays connections between frontend and sandbox agent server.

    Flow:
    1. Frontend connects: /ws/{port}/sockets/events/{id}
    2. Proxy connects to sandbox: ws://localhost:{port}/sockets/events/{id}
    3. Bidirectional message relay
    """
    await websocket.accept()

    # Get sandbox info from internal lookup
    # For now, assume sandbox is accessible at localhost:{port}
    # In production, use sandbox service to get actual URL

    sandbox_ws_url = f"ws://localhost:{sandbox_port}/sockets/events/{conversation_id}"

    _logger.info(f"WebSocket proxy: {sandbox_ws_url}")

    # Connect to sandbox WebSocket
    sandbox_ws = None
    try:
        # Use httpx for WebSocket connection (or websockets library)
        async with httpx.AsyncClient() as client:
            # Note: httpx doesn't support WebSocket directly
            # Need to use websockets library or custom implementation

            # Alternative: Use websockets library
            import websockets

            # Build query params for sandbox connection
            params = {}
            if session_api_key:
                params["session_api_key"] = session_api_key
            if resend_all:
                params["resend_all"] = "true"

            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            if query_string:
                sandbox_ws_url += f"?{query_string}"

            sandbox_ws = await websockets.connect(sandbox_ws_url)

            _logger.info(f"Connected to sandbox WebSocket: {sandbox_ws_url}")

            # Bidirectional relay
            async def relay_from_frontend():
                """Relay messages from frontend to sandbox."""
                try:
                    while True:
                        data = await websocket.receive_text()
                        await sandbox_ws.send(data)
                except WebSocketDisconnect:
                    _logger.info("Frontend disconnected")
                except Exception as e:
                    _logger.error(f"Error relaying from frontend: {e}")

            async def relay_from_sandbox():
                """Relay messages from sandbox to frontend."""
                try:
                    while True:
                        data = await sandbox_ws.recv()
                        await websocket.send_text(data)
                except Exception as e:
                    _logger.info(f"Sandbox WebSocket closed: {e}")

            # Run both relays concurrently
            await asyncio.gather(
                relay_from_frontend(),
                relay_from_sandbox(),
                return_exceptions=True,
            )

    except Exception as e:
        _logger.error(f"WebSocket proxy error: {e}")
        await websocket.close(code=1000, reason=str(e))

    finally:
        if sandbox_ws:
            await sandbox_ws.close()
        _logger.info(f"WebSocket proxy closed for {conversation_id}")
```

#### 2. Add Router to Main App

File: `openhands/app_server/v1_router.py`

```python
from openhands.app_server.websocket_proxy.websocket_proxy_router import router as ws_proxy_router

router.include_router(ws_proxy_router)  # WebSocket proxy routes
```

#### 3. Alternative: Use FastAPI WebSocket Route with Custom Protocol

```python
@router.websocket("/ws/{sandbox_port}/sockets/events/{conversation_id}")
async def websocket_proxy_alternative(websocket: WebSocket, sandbox_port: int, conversation_id: str):
    """
    Alternative implementation using raw WebSocket relay.
    """
    await websocket.accept()

    # For production: Use sandbox service to get connection details
    # sandbox = await sandbox_service.get_sandbox_by_port(sandbox_port)
    # sandbox_url = sandbox.internal_url or sandbox_url_pattern.format(port=sandbox_port)

    # For now: Direct localhost connection (works if on same machine)
    sandbox_url = f"ws://localhost:{sandbox_port}/sockets/events/{conversation_id}"

    # Need custom WebSocket client implementation
    # Using websockets library: pip install websockets

    import websockets

    try:
        async with websockets.connect(sandbox_url) as sandbox_ws:
            # Bidirectional relay
            async def client_to_sandbox():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.receive":
                        await sandbox_ws.send(msg.get("text", ""))
                    elif msg["type"] == "websocket.disconnect":
                        break

            async def sandbox_to_client():
                while True:
                    msg = await sandbox_ws.recv()
                    await websocket.send_text(msg)

            await asyncio.gather(client_to_sandbox(), sandbox_to_client())

    except Exception as e:
        _logger.error(f"Proxy error: {e}")
        await websocket.close()
```

## Considerations

### Option A: Direct Sandbox Connection (if same machine)
- App server connects to `localhost:{port}`
- Works if Docker and app server on same machine
- Requires `websockets` library

### Option B: Use Sandbox Internal URL
- App server uses `sandbox.internal_url` (from ExposedUrl)
- Respects proxy vs direct URL separation
- Works with both local and remote sandboxes

### Option C: Hybrid (Nginx + App Proxy)
- Nginx routes `/ws/{port}/` to app server (not sandbox)
- App server proxies to actual sandbox (internal_url)
- Nginx doesn't need to know random ports

## Implementation Steps

1. **Add websockets dependency** (if not already present)
```bash
pip install websockets
```

2. **Create websocket_proxy_router.py** in `openhands/app_server/websocket_proxy/`

3. **Include router in v1_router.py**

4. **Update SPAStaticFiles** to not intercept WebSocket (already done)

5. **Test WebSocket proxy**
```bash
# Frontend connects
wscat -c wss://domain.com/ws/59219/sockets/events/abc123

# App server proxies to
ws://localhost:59219/sockets/events/abc123
```

## Alternative Architecture

If sandbox is remote, use internal_url:

```python
# Get sandbox from conversation
conversation = await get_conversation(conversation_id)
sandbox = await sandbox_service.get_sandbox(conversation.sandbox_id)

# Use internal_url for proxy connection
agent_server_url = next(
    url.internal_url or url.url
    for url in sandbox.exposed_urls
    if url.name == AGENT_SERVER
)

# Connect to sandbox WebSocket
sandbox_ws_url = f"{agent_server_url}/sockets/events/{conversation_id}"
```

This works for both:
- Local sandbox: internal_url = `http://localhost:{port}` → proxy works
- Remote sandbox: internal_url = actual remote URL → proxy works

## Benefits

1. ✅ **No Nginx configuration needed** for random ports
2. ✅ **Single entry point** - all WebSocket through app server
3. ✅ **Centralized access control** - authentication at app server
4. ✅ **Works for remote sandboxes** - uses internal_url
5. ✅ **Logging and monitoring** - can track WebSocket connections

## Performance Considerations

- App server becomes WebSocket relay (adds latency)
- Need async/await for concurrent connections
- May need connection pooling for high traffic
- Consider using dedicated WebSocket proxy (like ws-tcp-relay)

## Testing

```bash
# 1. Start sandbox
# 2. Create conversation
# 3. Frontend connects: wss://domain.com/ws/59219/sockets/events/{id}
# 4. App server receives WebSocket
# 5. App server proxies to localhost:59219
# 6. Messages relay bidirectionally
```

## Security

- Validate sandbox_port is in allowed range (30000-39999)
- Validate conversation_id exists and belongs to user
- Add rate limiting for WebSocket connections
- Implement connection timeout and cleanup
