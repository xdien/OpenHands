# Nginx Proxy Configuration Examples

## Pattern A: `/ws/:{port}` (with colon separator)

```nginx
# Pattern: /ws/:34449 → localhost:34449
location ~ ^/ws/:(?<sandbox_port>\d+)/(.*)$ {
    proxy_pass http://localhost:$sandbox_port/$2;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Standard headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Timeout for long-running WebSocket connections
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;

    # Disable buffering for WebSocket
    proxy_buffering off;
}
```

**Environment Variable:**
```bash
SANDBOX_PROXY_URL_PATTERN=https://domain.com/ws/:{port}
```

## Pattern B: `/ws/{port}` (without colon separator) ← USER'S PATTERN

```nginx
# Pattern: /ws/34449 → localhost:34449
location ~ ^/ws/(?<sandbox_port>\d+)/(.*)$ {
    proxy_pass http://localhost:$sandbox_port/$2;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Standard headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Timeout for long-running WebSocket connections
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;

    # Disable buffering for WebSocket
    proxy_buffering off;
}
```

**Environment Variable:**
```bash
SANDBOX_PROXY_URL_PATTERN=https://domain.com/ws/{port}
```

**Note:** Pattern B is currently being used in production: `https://ai.canthotouring.com/ws/{port}`

## Complete Nginx Configuration Example

```nginx
# /etc/nginx/conf.d/openhands.conf

server {
    listen 80;
    listen 443 ssl http2;
    server_name ai.canthotouring.com;

    # SSL certificate
    ssl_certificate /etc/ssl/certs/ai.canthotouring.com.crt;
    ssl_certificate_key /etc/ssl/private/ai.canthotouring.com.key;

    # Main OpenHands application (port 3000)
    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Sandbox WebSocket and HTTP routing (Pattern B)
    location ~ ^/ws/(?<sandbox_port>\d+)/(.*)$ {
        proxy_pass http://localhost:$sandbox_port/$2;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Standard headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeout for WebSocket (24 hours)
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

## URL Routing Examples

### Pattern A (`/ws/:{port}`)

| Client Request | Nginx Routes To | Final Destination |
|---------------|----------------|-------------------|
| `GET /ws/:34449/alive` | `localhost:34449/alive` | Agent server health check |
| `GET /ws/:34449/api/conversations/abc123` | `localhost:34449/api/conversations/abc123` | Conversation API |
| `WS /ws/:34449/sockets/events/abc123` | `localhost:34449/sockets/events/abc123` | WebSocket connection |

### Pattern B (`/ws/{port}`)

| Client Request | Nginx Routes To | Final Destination |
|---------------|----------------|-------------------|
| `GET /ws/34449/alive` | `localhost:34449/alive` | Agent server health check |
| `GET /ws/34449/api/conversations/abc123` | `localhost:34449/api/conversations/abc123` | Conversation API |
| `WS /ws/34449/sockets/events/abc123` | `localhost:34449/sockets/events/abc123` | WebSocket connection |

## Testing Nginx Configuration

```bash
# Test configuration syntax
sudo nginx -t

# Check if pattern matches
# Pattern B
curl -I https://ai.canthotouring.com/ws/34449/alive

# Test WebSocket (use wscat)
wscat -c wss://ai.canthotouring.com/ws/34449/sockets/events/abc123
```

## Firewall Configuration

```bash
# Block direct port access (only allow proxy)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 30000:39999/tcp

# Or using iptables
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 30000:39999 -j DROP
```

## Environment Variables for Pattern B

```bash
# .env file
SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}
SANDBOX_PROXY_URL_PATTERN=https://ai.canthotouring.com/ws/{port}
```

## Benefits

1. ✅ **No random ports exposed** - Only 80/443 accessible from internet
2. ✅ **SSL/TLS centralization** - All encryption handled by Nginx
3. ✅ **Backend uses direct ports** - Reliable internal communication
4. ✅ **Frontend uses proxy** - Secure external access
5. ✅ **Single regex rule** - Handles all sandbox routing
6. ✅ **WebSocket support** - Long-running connections work

## Troubleshooting

### Issue: 405 Method Not Allowed

**Symptom:**
```
POST /ws/34449/api/bash/start_bash_command → 405
```

**Root Cause:** Backend using proxy URL for API calls

**Solution:** Backend now uses `internal_url` (direct port) instead of `url` (proxy)

**Expected Behavior After Fix:**
```
Backend: http://localhost:34449/api/bash/start_bash_command ✅
Frontend: https://ai.canthotouring.com/ws/34449/api/... (through proxy) ✅
```
