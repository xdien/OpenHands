# Proxy-Based Deployment Guide (No Code Changes Required)

## Overview

OpenHands V1 **already supports** proxy-based URL routing through the existing `SANDBOX_CONTAINER_URL_PATTERN` configuration. No code changes needed!

## Configuration Steps

### Step 1: Set Environment Variable

Add to your `.env` file or environment:

```bash
# Internal URL - for health checks and internal communication (REQUIRED)
SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}

# External URL - for frontend access through proxy (OPTIONAL)
# If not set, frontend will use SANDBOX_CONTAINER_URL_PATTERN
SANDBOX_PROXY_URL_PATTERN=https://your-domain.com/ws/:{port}
```

**Configuration Examples:**

| Scenario | Internal Pattern | External Pattern |
|----------|-----------------|------------------|
| **Production with proxy** | `http://localhost:{port}` | `https://domain.com/ws/:{port}` |
| **Staging with proxy** | `http://localhost:{port}` | `https://staging.domain.com/ws/:{port}` |
| **LAN deployment** | `http://192.168.1.100:{port}` | `https://192.168.1.100/ws/:{port}` |
| **Direct port mode (no proxy)** | `http://192.168.1.100:{port}` | *(not set)* |

**Important:**
- `SANDBOX_CONTAINER_URL_PATTERN` is ALWAYS used for internal health checks
- `SANDBOX_PROXY_URL_PATTERN` is only used for frontend external access
- If `SANDBOX_PROXY_URL_PATTERN` is not set, system falls back to direct port mode

### Step 2: Configure Reverse Proxy

#### Option A: Nginx Configuration

Create `/etc/nginx/conf.d/openhands-proxy.conf`:

```nginx
# Main OpenHands application
upstream openhands_app {
    server localhost:3000;
}

server {
    listen 80;
    listen 443 ssl http2;
    server_name your-domain.com;
    
    # SSL certificate (recommended)
    ssl_certificate /etc/ssl/certs/your-domain.com.crt;
    ssl_certificate_key /etc/ssl/private/your-domain.com.key;
    
    # Main application routes
    location / {
        proxy_pass http://openhands_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Sandbox WebSocket and HTTP routing
    # Pattern: /ws/:{port}/... → localhost:{port}/...
    location ~ ^/ws/:(?<sandbox_port>\d+)/(.*)$ {
        # Dynamic proxy to sandbox port
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
        
        # Long timeout for WebSocket connections (24 hours)
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        
        # Buffer settings for WebSocket
        proxy_buffering off;
    }
    
    # Optional: Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

#### Option B: Traefik Configuration (Docker Compose)

```yaml
# docker-compose.yml
version: '3'

services:
  openhands:
    image: ghcr.io/all-hands-ai/openhands:latest
    environment:
      - SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}
      - SANDBOX_PROXY_URL_PATTERN=https://${DOMAIN}/ws/:{port}
      - SANDBOX_HOST_PORT=3000
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.openhands.rule=PathPrefix(`/`)"
      - "traefik.http.routers.openhands.entrypoints=websecure"
      - "traefik.http.routers.openhands.tls=true"
      
  traefik:
    image: traefik:v2.10
    command:
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./traefik-dynamic.yml:/etc/traefik/dynamic.yml
```

Create `traefik-dynamic.yml`:

```yaml
http:
  routers:
    sandbox-dynamic:
      rule: "PathRegexp(`^/ws/:[0-9]+/`)"
      service: sandbox-service
      entryPoints:
        - websecure
      tls: true
  
  services:
    sandbox-service:
      loadBalancer:
        passHostHeader: true
        # Note: Traefik doesn't support dynamic port routing natively
        # You may need a custom middleware or use Nginx instead
```

**Recommendation:** Use Nginx for sandbox routing, Traefik for main app.

#### Option C: Apache with mod_proxy

```apache
# /etc/apache2/sites-available/openhands.conf

<VirtualHost *:443>
    ServerName your-domain.com
    
    SSLEngine on
    SSLCertificateFile /etc/ssl/certs/your-domain.com.crt
    SSLCertificateKeyFile /etc/ssl/private/your-domain.com.key
    
    # Main application
    ProxyPass / http://localhost:3000/
    ProxyPassReverse / http://localhost:3000/
    
    # Sandbox routing with regex (requires mod_proxy and mod_rewrite)
    RewriteEngine On
    RewriteCond %{REQUEST_URI} ^/ws/:[0-9]+/(.*)$
    RewriteRule ^/ws/:[0-9]+/(.*)$ http://localhost:${MATCH_PORT}/$1 [P,L]
    
    # WebSocket support
    ProxyPreserveHost On
    ProxyPassReverseCookiePath / /
</VirtualHost>
```

### Step 3: Firewall Configuration

Only expose standard ports, block random sandbox ports:

```bash
# UFW (Ubuntu/Debian)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 30000:39999/tcp  # Block sandbox port range

# iptables
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 30000:39999 -j DROP
```

### Step 4: Restart Services

```bash
# Restart OpenHands application
sudo systemctl restart openhands

# Restart Nginx
sudo nginx -t  # Test configuration
sudo systemctl restart nginx
```

## How It Works

### URL Transformation Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Backend (with SANDBOX_CONTAINER_URL_PATTERN)             │
│    Returns: https://domain.com/ws/:45663                    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Frontend receives                                         │
│    conversation_url: https://domain.com/ws/:45663/api/...   │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Frontend transforms (existing code works!)               │
│    ws_url: wss://domain.com/ws/:45663/sockets/events/...    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Nginx routes                                              │
│    /ws/:45663/... → localhost:45663/...                     │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Agent Server receives                                     │
│    WebSocket on port 45663                                   │
└─────────────────────────────────────────────────────────────┘
```

### Frontend Code (Already Works!)

The existing frontend code in `websocket-url.ts`:

```typescript
// extractPathPrefix() correctly extracts: /ws/:45663
const pathPrefix = extractPathPrefix('https://domain.com/ws/:45663/api/...');
// Result: "/ws/:45663"

// buildWebSocketUrl() correctly builds:
const wsUrl = buildWebSocketUrl('abc123', conversationUrl);
// Result: "wss://domain.com/ws/:45663/sockets/events/abc123"
```

## Testing

### Test Backend Configuration

```bash
# Check if environment variable is set
echo $SANDBOX_CONTAINER_URL_PATTERN

# Test with curl (after starting a conversation)
curl -H "Authorization: Bearer $API_KEY" \
  https://your-domain.com/api/v1/conversations/{id}
# Should return conversation_url with /ws/:{port}/ pattern
```

### Test Proxy Routing

```bash
# Test HTTP routing
curl https://your-domain.com/ws/:45663/api/conversations/test

# Test WebSocket routing (use wscat)
wscat -c wss://your-domain.com/ws/:45663/sockets/events/test
```

### Test Frontend

Open browser DevTools Network tab:
1. Start a conversation
2. Check WebSocket connection URL
3. Should show: `wss://your-domain.com/ws/:{port}/sockets/events/{id}`

## Security Benefits

| Feature | Direct Port Mode | Proxy Mode |
|---------|------------------|------------|
| Ports exposed to internet | 30000-39999 (risk) | Only 80/443 (safe) |
| SSL/TLS management | Per port (complex) | Centralized (simple) |
| Access control | Per port (difficult) | Centralized (easy) |
| Firewall configuration | Open many ports | Open 2 ports |
| DDoS protection | None | Via proxy |
| Rate limiting | None | Via proxy |
| Audit logging | Distributed | Centralized |

## Common Issues

### Issue 1: Nginx regex not matching

**Symptom:** 404 errors for `/ws/:45663/` paths

**Solution:** Check nginx regex syntax:
```nginx
# Correct (with named capture)
location ~ ^/ws/:(?<sandbox_port>\d+)/(.*)$ {
    proxy_pass http://localhost:$sandbox_port/$2;
}

# Wrong (missing named capture)
location ~ ^/ws/:\d+/ {
    # Won't work - can't extract port
}
```

### Issue 2: WebSocket connection fails

**Symptom:** WebSocket disconnects immediately

**Solution:** Add WebSocket headers:
```nginx
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 86400;  # Long timeout
```

### Issue 3: Frontend still uses localhost

**Symptom:** Frontend shows `ws://localhost:45663`

**Solution:** Check environment variable is set **before** starting app:
```bash
# Must be set before app starts
export SANDBOX_CONTAINER_URL_PATTERN=https://domain.com/ws/:{port}
sudo systemctl restart openhands
```

## Migration from Direct Port Mode

### Current Setup (Direct Port)
```bash
# .env
SANDBOX_CONTAINER_URL_PATTERN=http://192.168.1.100:{port}
```

### New Setup (Proxy Mode)
```bash
# .env
SANDBOX_CONTAINER_URL_PATTERN=https://domain.com/ws/:{port}
```

### Migration Steps
1. Deploy Nginx proxy configuration
2. Update `.env` with new URL pattern
3. Restart OpenHands application
4. Test WebSocket connections
5. Close firewall for port range 30000-39999

**Rollback:** Simply revert `.env` and restart.

## Performance Considerations

### Nginx Proxy Settings

```nginx
# For high-traffic deployments
worker_processes auto;
worker_connections 4096;

# Connection pooling
upstream sandbox_pool {
    server localhost:30000-39999;
    keepalive 128;
}

# Caching (for static content only)
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=sandbox_cache:10m;
```

### Monitoring

```nginx
# Add access logging
log_format sandbox '$remote_addr - $remote_user [$time_local] '
                    '"$request" $status $body_bytes_sent '
                    '"$http_referer" "$http_user_agent" '
                    'port:$sandbox_port';

access_log /var/log/nginx/sandbox.log sandbox;
```

## Complete Example Deployment

### Production Setup

```bash
# 1. Install Nginx
sudo apt update
sudo apt install nginx

# 2. Create Nginx config
sudo nano /etc/nginx/sites-available/openhands.conf
# (copy configuration from above)

# 3. Enable site
sudo ln -s /etc/nginx/sites-available/openhands.conf \
           /etc/nginx/sites-enabled/

# 4. Set environment variables
sudo nano /opt/openhands/.env
```

```env
SANDBOX_CONTAINER_URL_PATTERN=https://openhands.yourcompany.com/ws/:{port}
SANDBOX_HOST_PORT=3000
SANDBOX_STARTUP_GRACE_SECONDS=30
SANDBOX_MAX_NUM_SANDBOXES=5
```

```bash
# 5. Test Nginx
sudo nginx -t

# 6. Configure firewall
sudo ufw allow 'Nginx Full'
sudo ufw deny 30000:39999/tcp

# 7. Restart services
sudo systemctl restart openhands
sudo systemctl restart nginx

# 8. Verify
curl -I https://openhands.yourcompany.com/
curl https://openhands.yourcompany.com/api/v1/sandboxes
```

## Summary

✅ **No code changes needed**
✅ **Existing frontend functions work perfectly**
✅ **Single environment variable configuration**
✅ **Nginx handles all routing**
✅ **Secure: only 80/443 exposed**
✅ **Easy rollback**

**Total deployment time:** ~30 minutes (mostly Nginx config)

---

## Next Steps

1. Set `SANDBOX_CONTAINER_URL_PATTERN` environment variable
2. Deploy Nginx proxy configuration
3. Restart OpenHands application
4. Test WebSocket connections
5. Configure firewall to block port range 30000-39999

**Questions?** Check the architecture diagram in `.pr/proxy-architecture-design.md`