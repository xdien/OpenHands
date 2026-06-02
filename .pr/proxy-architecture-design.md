# Proxy-Based Architecture Design for V1 Sandbox Connections

## Problem Statement

Current V1 architecture exposes random ports (30000-39999) directly to clients:
```
Browser → ws://domain.com:45663/sockets/events/{conversationId}
```

This creates security risks:
- Random ports exposed to internet
- No centralized access control
- Difficult SSL/TLS management
- Firewall configuration complexity

## Proposed Solution

Use a reverse proxy with path-based routing instead of port-based routing:

```
Browser → wss://domain.com/ws/{port}/sockets/events/{conversationId}
Proxy → ws://localhost:{port}/sockets/events/{conversationId}
```

## URL Schema Design

### Current Format
```
conversation_url: http://localhost:45663/api/conversations/abc123
ws_url: ws://localhost:45663/sockets/events/abc123
```

### New Format (Proxy-Based)
```
# Backend returns with port info embedded
exposed_url.url: http://localhost:45663  (unchanged for internal)

# Frontend transforms for external access
conversation_url: https://domain.com/ws/45663/api/conversations/abc123
ws_url: wss://domain.com/ws/45663/sockets/events/abc123
```

### URL Transformation Rules

| Original (Backend) | Transformed (Frontend) | Proxy Route |
|--------------------|------------------------|-------------|
| `http://host:45663/api/...` | `https://domain.com/ws/45663/api/...` | `/ws/45663/` → `localhost:45663` |
| `ws://host:45663/sockets/...` | `wss://domain.com/ws/45663/sockets/...` | `/ws/45663/` → `localhost:45663` |

## Implementation Plan

### Phase 1: Backend Changes

#### 1.1 Add New Configuration Option

File: `openhands/app_server/sandbox/docker_sandbox_service.py`

Add new field `proxy_url_pattern` to `DockerSandboxServiceInjector`:

```python
proxy_url_pattern: str | None = Field(
    default=None,
    description=(
        'URL pattern for proxy-based access. Use {port} as placeholder. '
        'When set, frontend will use this pattern for external access. '
        'Example: https://domain.com/ws/{port} '
        'Configure via OH_SANDBOX_PROXY_URL_PATTERN environment variable.'
    ),
)
```

#### 1.2 Modify URL Construction

File: `openhands/app_server/sandbox/docker_sandbox_service.py`

Update `_container_to_sandbox_info()` to include both patterns:

```python
# Build exposed URLs
for exposed_port in self.exposed_ports:
    # Internal URL (for Docker internal communication)
    internal_url = self.container_url_pattern.format(port=host_port)

    # External URL (for client access) - if proxy pattern is configured
    external_url = None
    if self.proxy_url_pattern:
        external_url = self.proxy_url_pattern.format(port=host_port)
    else:
        external_url = internal_url  # fallback to direct port access

    exposed_urls.append(
        ExposedUrl(
            name=matching_port.name,
            url=external_url,  # Return external URL for client
            port=matching_port.container_port,
            internal_url=internal_url,  # Optional: for internal use
        )
    )
```

#### 1.3 Update ExposedUrl Model

File: `openhands/app_server/sandbox/sandbox_models.py`

```python
class ExposedUrl(BaseModel):
    """URL to access some named service within the container."""

    name: str
    url: str  # External URL for client access
    port: int
    internal_url: str | None = None  # Optional internal URL
```

#### 1.4 Environment Variable Configuration

File: `openhands/app_server/config.py`

Add handling for `OH_SANDBOX_PROXY_URL_PATTERN`:

```python
if os.getenv('OH_SANDBOX_PROXY_URL_PATTERN'):
    docker_sandbox_kwargs['proxy_url_pattern'] = os.environ[
        'OH_SANDBOX_PROXY_URL_PATTERN'
    ]
```

### Phase 2: Frontend Changes

#### 2.1 Update WebSocket URL Builder

File: `frontend/src/utils/websocket-url.ts`

Add function to detect and transform proxy URLs:

```typescript
/**
 * Extracts port from URL if it's in port-based format
 * @param conversationUrl The conversation URL
 * @returns Port number if found, null otherwise
 */
export function extractPortFromUrl(
  conversationUrl: string | null | undefined,
): number | null {
  if (!conversationUrl || conversationUrl.startsWith("/")) {
    return null;
  }

  try {
    const url = new URL(conversationUrl);
    // Check if URL has explicit port (not default 80/443)
    if (url.port && url.port !== "80" && url.port !== "443") {
      return parseInt(url.port, 10);
    }
    // Check if path contains /ws/{port}/ pattern
    const wsPortMatch = url.pathname.match(/^\/ws\/(\d+)/);
    if (wsPortMatch) {
      return parseInt(wsPortMatch[1], 10);
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Transforms port-based URL to proxy-based URL
 * @param conversationUrl Original URL with port
 * @returns Transformed URL with /ws/{port}/ path
 */
export function transformToProxyUrl(
  conversationUrl: string | null | undefined,
): string | null {
  if (!conversationUrl || conversationUrl.startsWith("/")) {
    return conversationUrl;
  }

  try {
    const url = new URL(conversationUrl);
    const port = extractPortFromUrl(conversationUrl);

    if (!port) {
      // No port detected, use as-is
      return conversationUrl;
    }

    // Get browser's current protocol and host
    const protocol = window.location.protocol;
    const host = window.location.host;

    // Transform: http://host:45663/api/... → https://domain/ws/45663/api/...
    const newPath = `/ws/${port}${url.pathname}`;

    return `${protocol}//${host}${newPath}`;
  } catch {
    return conversationUrl;
  }
}

/**
 * Updated buildWebSocketUrl with proxy support
 */
export function buildWebSocketUrl(
  conversationId: string | undefined,
  conversationUrl: string | null | undefined,
): string | null {
  if (!conversationId) {
    return null;
  }

  // Transform URL if it has explicit port
  const transformedUrl = transformToProxyUrl(conversationUrl);

  if (!transformedUrl) {
    return null;
  }

  const url = new URL(transformedUrl);
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

  // WebSocket path: /ws/{port}/sockets/events/{conversationId}
  const wsPath = url.pathname.replace("/api/conversations/", "/sockets/events/");

  return `${protocol}//${url.host}${wsPath}${conversationId}`;
}
```

#### 2.2 Update HTTP Base URL Builder

File: `frontend/src/utils/websocket-url.ts`

```typescript
export function buildHttpBaseUrl(
  conversationUrl: string | null | undefined,
): string {
  // Transform to proxy URL first
  const transformedUrl = transformToProxyUrl(conversationUrl);

  if (!transformedUrl) {
    return `${window.location.protocol}//${window.location.host}`;
  }

  try {
    const url = new URL(transformedUrl);
    const protocol = window.location.protocol;

    // Extract base path (without /api/conversations)
    const basePath = url.pathname.split("/api/conversations")[0] || "";

    return `${protocol}//${url.host}${basePath}`;
  } catch {
    return `${window.location.protocol}//${window.location.host}`;
  }
}
```

#### 2.3 Update API Types

File: `frontend/src/api/sandbox-service/sandbox-service.types.ts`

```typescript
export interface V1ExposedUrl {
  name: string;
  url: string;
  port: number;
  internal_url?: string | null;
}
```

### Phase 3: Proxy Configuration

#### 3.1 Nginx Configuration Example

```nginx
# /etc/nginx/conf.d/openhands.conf

# Main application server
location / {
    proxy_pass http://localhost:3000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}

# Dynamic sandbox WebSocket routing
location ~ ^/ws/(?<sandbox_port>\d+)/sockets/events/(?<conversation_id>.+)$ {
    proxy_pass http://localhost:$sandbox_port/sockets/events/$conversation_id;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 86400;  # 24 hours for long-running connections
}

# Dynamic sandbox HTTP routing
location ~ ^/ws/(?<sandbox_port>\d+)/api/ {
    proxy_pass http://localhost:$sandbox_port/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# VSCode proxy routing
location ~ ^/ws/(?<sandbox_port>\d+)/vscode/ {
    proxy_pass http://localhost:$sandbox_port/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}

# Worker app proxy routing
location ~ ^/ws/(?<sandbox_port>\d+)/worker/ {
    proxy_pass http://localhost:$sandbox_port/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
}
```

#### 3.2 Traefik Configuration Example

```yaml
# traefik.yml

http:
  routers:
    # Main app
    openhands-main:
      rule: "PathPrefix(`/`)"
      service: openhands-app
      middlewares:
        - strip-ws-prefix
      entryPoints:
        - websecure
      tls: true

    # Dynamic sandbox routing
    openhands-sandbox-ws:
      rule: "PathRegexp(`^/ws/[0-9]+/`)"
      service: sandbox-dynamic
      entryPoints:
        - websecure
      tls: true

  services:
    openhands-app:
      loadBalancer:
        servers:
          - url: "http://localhost:3000"

    sandbox-dynamic:
      loadBalancer:
        # Dynamic routing based on port in path
        servers:
          - url: "http://localhost:{port}"
        passHostHeader: true

  middlewares:
    strip-ws-prefix:
      stripPrefixRegex:
        regex:
          - "^/ws/[0-9]+/"
```

#### 3.3 HAProxy Configuration Example

```
# haproxy.cfg

frontend https_front
    bind *:443 ssl crt /etc/ssl/certs/openhands.pem

    # ACL for sandbox WebSocket
    acl sandbox_ws path_beg /ws/
    acl is_ws hdr(Upgrade) -i WebSocket

    # Extract port from path
    use_backend sandbox_dynamic if sandbox_ws

    # Default backend
    default_backend openhands_app

backend openhands_app
    balance roundrobin
    option http-server-close
    option forwardfor
    server app1 localhost:3000

backend sandbox_dynamic
    # Dynamic backend routing based on path
    # Note: Requires Lua scripting or external check
    # This is a simplified example
    balance roundrobin
    option http-server-close
    option forwardfor
    http-request set-header X-Sandbox-Port %[path,regsub(^/ws/([0-9]+)/,.+,\1)]
    # Route to appropriate port based on header
```

### Phase 4: Migration Strategy

#### 4.1 Backward Compatibility

The system should support both modes:

1. **Direct Port Mode** (current): Used when `proxy_url_pattern` is not set
2. **Proxy Path Mode** (new): Used when `proxy_url_pattern` is configured

```python
# Backend logic
if self.proxy_url_pattern:
    # Return proxy-based URL
    url = self.proxy_url_pattern.format(port=host_port)
else:
    # Return direct port URL (backward compatible)
    url = self.container_url_pattern.format(port=host_port)
```

```typescript
// Frontend logic
const port = extractPortFromUrl(conversationUrl);
if (port && window.location.host !== originalHost) {
    // Transform to proxy URL
    url = transformToProxyUrl(conversationUrl);
} else {
    // Use direct URL (backward compatible)
    url = conversationUrl;
}
```

#### 4.2 Deployment Steps

1. **Deploy proxy configuration** (nginx/traefik) first
2. **Set environment variable** `OH_SANDBOX_PROXY_URL_PATTERN=https://domain.com/ws/{port}`
3. **Restart app server** to pick up new configuration
4. **Frontend automatically detects** proxy mode and transforms URLs
5. **Verify WebSocket connections** through proxy

#### 4.3 Rollback Plan

If issues arise:
1. Remove `OH_SANDBOX_PROXY_URL_PATTERN` environment variable
2. Restart app server
3. System automatically falls back to direct port mode
4. Remove proxy routing configuration

### Phase 5: Testing

#### 5.1 Backend Tests

File: `tests/unit/app_server/test_docker_sandbox_service.py`

```python
def test_proxy_url_pattern_configured():
    """Test that proxy_url_pattern is used when configured."""
    injector = DockerSandboxServiceInjector(
        proxy_url_pattern='https://domain.com/ws/{port}'
    )
    assert injector.proxy_url_pattern == 'https://domain.com/ws/{port}'

def test_exposed_url_with_proxy_pattern():
    """Test that exposed URLs use proxy pattern."""
    service = DockerSandboxService(
        container_url_pattern='http://localhost:{port}',
        proxy_url_pattern='https://domain.com/ws/{port}',
        ...
    )
    sandbox_info = await service._container_to_sandbox_info(container)

    # External URL should use proxy pattern
    agent_url = next(u for u in sandbox_info.exposed_urls if u.name == AGENT_SERVER)
    assert 'domain.com/ws/45663' in agent_url.url

    # Internal URL should use container pattern
    assert 'localhost:45663' in agent_url.internal_url
```

#### 5.2 Frontend Tests

File: `frontend/__tests__/utils/websocket-url.test.ts`

```typescript
describe('transformToProxyUrl', () => {
  it('transforms port-based URL to proxy URL', () => {
    const original = 'http://localhost:45663/api/conversations/abc123';
    const result = transformToProxyUrl(original);
    expect(result).toBe('https://example.com/ws/45663/api/conversations/abc123');
  });

  it('handles URLs without explicit port', () => {
    const original = 'https://domain.com/api/conversations/abc123';
    const result = transformToProxyUrl(original);
    expect(result).toBe(original); // unchanged
  });
});

describe('buildWebSocketUrl with proxy', () => {
  it('builds correct WebSocket URL with proxy path', () => {
    const conversationId = 'abc123';
    const conversationUrl = 'http://localhost:45663/api/conversations/abc123';
    const result = buildWebSocketUrl(conversationId, conversationUrl);
    expect(result).toBe('wss://example.com/ws/45663/sockets/events/abc123');
  });
});
```

### Phase 6: Security Considerations

#### 6.1 Benefits

1. **Single entry point**: All traffic through reverse proxy
2. **SSL/TLS termination**: Certificate management at proxy level
3. **Access control**: Rate limiting, IP filtering at proxy
4. **Audit logging**: Centralized request logging
5. **No random ports exposed**: Only standard 80/443 to internet

#### 6.2 Additional Security Recommendations

```nginx
# Rate limiting
limit_req_zone $binary_remote_addr zone=ws_limit:10m rate=10r/s;
limit_req zone=ws_limit burst=20 nodelay;

# IP whitelisting (optional)
location ~ ^/ws/ {
    allow 10.0.0.0/8;    # Internal network
    allow 192.168.0.0/16; # VPN
    deny all;

    # ... rest of proxy config
}
```

## Summary

| Component | Change | Priority |
|-----------|--------|----------|
| Backend Config | Add `proxy_url_pattern` field | High |
| Backend Service | Modify URL construction | High |
| Backend Model | Update `ExposedUrl` with `internal_url` | Medium |
| Frontend Utils | Add URL transformation functions | High |
| Frontend Types | Update TypeScript interfaces | Medium |
| Proxy Config | Add nginx/traefik routing rules | High |
| Tests | Add unit tests for new logic | Medium |
| Docs | Update deployment documentation | Low |

## Environment Variables

```bash
# Enable proxy-based URLs
OH_SANDBOX_PROXY_URL_PATTERN=https://domain.com/ws/{port}

# Optional: Override default container URL pattern (for internal Docker communication)
OH_SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}

# Optional: Host port for webhook callbacks
OH_SANDBOX_HOST_PORT=3000
```

## Timeline Estimate

| Phase | Duration |
|-------|----------|
| Backend Changes | 2-3 days |
| Frontend Changes | 2-3 days |
| Proxy Configuration | 1-2 days |
| Testing | 1-2 days |
| Documentation | 1 day |
| **Total** | **7-11 days** |
