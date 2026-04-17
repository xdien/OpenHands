# Proxy URL Pattern Implementation Summary

## Problem

When using `SANDBOX_CONTAINER_URL_PATTERN=http://localhost/ws/:{port}`, backend health check failed because:
- Backend tried to connect to `http://localhost/ws/:36375/health` (proxy URL)
- But Nginx proxy routing doesn't work from backend (internal health check)
- Health check needs direct port access: `http://localhost:36375/health`

## Solution

Split URLs into two patterns:
1. **Internal URL** (`container_url_pattern`) - for health checks and internal communication
2. **External URL** (`proxy_url_pattern`) - for frontend client access

## Implementation Changes

### 1. Backend: Add `proxy_url_pattern` field

**File:** `openhands/app_server/sandbox/docker_sandbox_service.py`

```python
# Added new field to DockerSandboxServiceInjector
proxy_url_pattern: str | None = Field(
    default=None,
    description=(
        'URL pattern for external sandbox access (frontend). '
        'Use {port} as placeholder. When set, frontend will use this pattern for client access. '
        'Example: https://domain.com/ws/:{port} for proxy-based routing. '
        'Configure via OH_SANDBOX_PROXY_URL_PATTERN environment variable. '
        'If not set, falls back to container_url_pattern.'
    ),
)
```

### 2. Backend Model: Add `internal_url` field

**File:** `openhands/app_server/sandbox/sandbox_models.py`

```python
# Updated ExposedUrl model
class ExposedUrl(BaseModel):
    name: str
    url: str  # External URL (for frontend)
    port: int
    internal_url: str | None = None  # Internal URL (for health checks)
```

### 3. Backend: Add helper method to build both URLs

**File:** `openhands/app_server/sandbox/docker_sandbox_service.py`

```python
def _build_exposed_url(self, name, port, host_port, session_api_key, container):
    """Build ExposedUrl with internal and external URLs."""
    # Internal URL (always direct port access)
    internal_url = self.container_url_pattern.format(port=host_port)
    
    # External URL (proxy pattern if configured, else fallback to internal)
    if self.proxy_url_pattern:
        external_url = self.proxy_url_pattern.format(port=host_port)
    else:
        external_url = internal_url
    
    # VSCode URLs require authentication params
    if name == VSCODE:
        vscode_params = f"/?tkn={session_api_key}&folder={...}"
        internal_url += vscode_params
        external_url += vscode_params
    
    return ExposedUrl(
        name=name,
        url=external_url,  # For frontend
        port=port,
        internal_url=internal_url if self.proxy_url_pattern else None,
    )
```

### 4. Backend: Update URL building logic

**File:** `openhands/app_server/sandbox/docker_sandbox_service.py`

- Replaced direct URL building with `_build_exposed_url()` helper
- Both host network mode and bridge network mode now use the same helper

### 5. Backend: Update health check logic

**File:** `openhands/app_server/sandbox/docker_sandbox_service.py`

```python
async def _container_to_checked_sandbox_info(self, container):
    # Get app server exposed URL
    app_server_exposed_url = next(
        exposed_url
        for exposed_url in sandbox_info.exposed_urls
        if exposed_url.name == AGENT_SERVER
    )
    
    # Use internal_url for health check if available (proxy mode)
    # Otherwise use url (direct port mode)
    app_server_url = (
        app_server_exposed_url.internal_url or app_server_exposed_url.url
    )
    
    # Health check with direct port URL
    response = await self.httpx_client.get(f"{app_server_url}/health")
```

### 6. Backend Config: Load `proxy_url_pattern` from environment

**File:** `openhands/app_server/config.py`

```python
if os.getenv('SANDBOX_PROXY_URL_PATTERN'):
    docker_sandbox_kwargs['proxy_url_pattern'] = os.environ[
        'SANDBOX_PROXY_URL_PATTERN'
    ]
```

## Configuration

### Environment Variables

```bash
# REQUIRED: Internal URL for health checks
SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}

# OPTIONAL: External URL for frontend (proxy mode)
SANDBOX_PROXY_URL_PATTERN=https://your-domain.com/ws/:{port}
```

### Examples

**Proxy Mode (recommended):**
```bash
export SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}
export SANDBOX_PROXY_URL_PATTERN=https://domain.com/ws/:{port}
```

**Direct Port Mode (fallback):**
```bash
export SANDBOX_CONTAINER_URL_PATTERN=http://192.168.1.100:{port}
# No SANDBOX_PROXY_URL_PATTERN set
```

## URL Flow

### Proxy Mode (with both patterns set)

```
Backend builds ExposedUrl:
├─ url (external): https://domain.com/ws/:36375  (for frontend)
└─ internal_url: http://localhost:36375          (for health check)

Frontend receives:
├─ conversation_url: https://domain.com/ws/:36375/api/conversations/abc123
└─ WebSocket URL: wss://domain.com/ws/:36375/sockets/events/abc123

Backend health check:
├─ Uses internal_url: http://localhost:36375/health  ✅
└─ Direct port connection works

Nginx proxy:
├─ Routes: /ws/:36375/... → localhost:36375/...  ✅
└─ Proxy routing for frontend
```

### Direct Port Mode (without proxy pattern)

```
Backend builds ExposedUrl:
├─ url (external): http://192.168.1.100:36375  (for frontend)
└─ internal_url: None                          (not set)

Frontend receives:
├─ conversation_url: http://192.168.1.100:36375/api/conversations/abc123
└─ WebSocket URL: ws://192.168.1.100:36375/sockets/events/abc123

Backend health check:
├─ Uses url (fallback): http://192.168.1.100:36375/health  ✅
└─ Direct port connection works

No proxy needed:
├─ Frontend connects directly to port 36375  ✅
```

## Backward Compatibility

- ✅ Existing deployments without `SANDBOX_PROXY_URL_PATTERN` continue to work
- ✅ `SANDBOX_CONTAINER_URL_PATTERN` is still used for internal communication
- ✅ If `proxy_url_pattern` is not set, system falls back to direct port mode
- ✅ Frontend code unchanged (already supports path-based URLs)

## Testing

### Test Script

Run the test script:
```bash
chmod +x .pr/test-proxy-config.sh
./.pr/test-proxy-config.sh
```

### Manual Test

1. **Set environment variables:**
```bash
export SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}
export SANDBOX_PROXY_URL_PATTERN=http://localhost/ws/:{port}
```

2. **Start OpenHands:**
```bash
make run
```

3. **Create a conversation:**
- Open frontend UI
- Start new conversation
- Check logs for URL building

4. **Verify URLs:**
```bash
# Should see in logs:
# Internal URL: http://localhost:36375/health (health check)
# External URL: http://localhost/ws/:36375 (frontend)
```

## Files Changed

| File | Changes | Lines |
|------|---------|-------|
| `docker_sandbox_service.py` | Added `proxy_url_pattern` to dataclass (moved to correct position) | +1 |
| `docker_sandbox_service.py` | Reordered dataclass fields (required before optional) | ~17 (moved) |
| `docker_sandbox_service.py` | Added `proxy_url_pattern` to Injector | +14 |
| `docker_sandbox_service.py` | Added `_build_exposed_url()` helper | +45 |
| `docker_sandbox_service.py` | Updated URL building logic | ~30 (refactored) |
| `docker_sandbox_service.py` | Updated health check logic | +5 |
| `docker_sandbox_service.py` | Added to inject() method (moved to correct position) | +1 |
| `sandbox_models.py` | Added `internal_url` field | +6 |
| `config.py` | Added env var loading | +4 |
| **Total** | | **~123 lines** |

## Python Dataclass Ordering Rule

**Important:** Fields with default values MUST come AFTER fields without default values.

```python
@dataclass
class DockerSandboxService:
    # Required fields (no default) - MUST be first
    sandbox_spec_service: SandboxSpecService
    container_name_prefix: str
    container_url_pattern: str
    mounts: list[VolumeMount]
    ...
    
    # Optional fields (with default) - MUST be last
    proxy_url_pattern: str | None = None  # ✅ after required
    web_url: str | None = None
    use_host_network: bool = False
    ...
```

## URL Pattern Variants

There are two supported URL patterns for proxy routing:

### Pattern A: `/ws/:{port}` (with colon separator)
```
Example: https://domain.com/ws/:34449
Nginx regex: location ~ ^/ws/:(?<port>\d+)/
```

### Pattern B: `/ws/{port}` (without colon separator)
```
Example: https://domain.com/ws/34449
Nginx regex: location ~ ^/ws/(?<port>\d+)/
```

**Current implementation supports both patterns.**

The user's deployment uses Pattern B: `SANDBOX_PROXY_URL_PATTERN=https://ai.canthotouring.com/ws/{port}`

## Backend vs Frontend URL Usage

### Backend Operations (Use internal_url)

Backend operations that need direct port access:
- **Execute bash commands** - `AsyncRemoteWorkspace` API calls
- **File download/upload** - File operations through agent server
- **Health checks** - Checking if agent server is running
- **Internal API calls** - Any backend-to-agent-server communication

These use `internal_url` (direct port) for reliable internal connectivity.

### Frontend Operations (Use url)

Frontend operations that go through proxy:
- **WebSocket connections** - Real-time event streaming
- **UI display** - Showing URLs to users
- **Client-side API calls** - Requests from browser (if needed)

These use `url` (proxy path) for secure external access through reverse proxy.

## Files Changed for Backend URL Usage

| File | Function | Change | Purpose |
|------|----------|--------|---------|
| `live_status_app_conversation_service.py` | `_get_agent_server_url()` | Use `internal_url` or fallback to `url` | Execute bash commands, setup scripts |
| `app_conversation_router.py` | AgentServerContext | Use `internal_url` or fallback to `url` | Context for agent server operations |
| `app_conversation_router.py` | File download endpoint | Use `internal_url` or fallback to `url` | Download files from sandbox |

## Benefits

1. ✅ **Health check works** - uses direct port URL
2. ✅ **Frontend uses proxy** - uses path-based URL
3. ✅ **Backward compatible** - works without proxy pattern
4. ✅ **No frontend changes** - existing code already supports paths
5. ✅ **Secure** - random ports not exposed to internet (with proxy)
6. ✅ **Simple config** - just 2 environment variables

## Next Steps

1. Set environment variables in `.env`
2. Configure Nginx proxy routing (see `.pr/proxy-deployment-guide.md`)
3. Restart OpenHands application
4. Test WebSocket connections through proxy
5. Block firewall ports 30000-39999 (only 80/443 exposed)

## References

- Design document: `.pr/proxy-architecture-design.md`
- Deployment guide: `.pr/proxy-deployment-guide.md`
- Test script: `.pr/test-proxy-config.sh`
## Troubleshooting

### Issue 1: AttributeError: 'DockerSandboxService' has no attribute 'proxy_url_pattern'

**Solution:** Added `proxy_url_pattern` to `DockerSandboxService` dataclass and `inject()` method.

### Issue 2: TypeError: non-default argument follows default argument

**Solution:** Reordered dataclass fields - required fields first, optional fields last.

### Issue 3: AssertionError in StaticFiles for WebSocket requests

**Symptom:**
```
File "starlette/staticfiles.py", line 91, in __call__
    assert scope["type"] == "http"
AssertionError
```

**Root Cause:**
- WebSocket requests hitting SPAStaticFiles mounted at '/' in main app server
- StaticFiles only handles HTTP requests, not WebSocket
- Nginx proxy not configured to route `/ws/{port}/` to sandbox

**Solution:** 
1. **Code fix:** Override `__call__()` in SPAStaticFiles to gracefully close WebSocket requests (prevents AssertionError)
2. **Deployment fix:** Configure Nginx to route `/ws/{port}/` → `localhost:{port}` (routes WebSocket to sandbox)

**Files Changed:**
- `openhands/server/static.py` - Added WebSocket handling in SPAStaticFiles
