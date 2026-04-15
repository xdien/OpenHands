# Running OpenHands Enterprise with SSO

## Prerequisites

- Docker & Docker Compose installed
- PostgreSQL running and accessible
- Redis running and accessible
- An SSO/OAuth2 backend (e.g., Keycloak, Auth0, or custom auth service)

## Configuration

### 1. Environment Variables

Create `enterprise/.env` file with the following configuration:

```bash
# === Backend Configuration ===
BACKEND_PORT=3009
OPENHANDS_CONFIG_CLS=server.config.SaaSServerConfig

# === Force SaaS mode for web client ===
OH_APP_MODE=saas

# === Database Configuration ===
DB_TYPE=postgres
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASS=your_database_password
DB_NAME=openhands
DB_SYNCHRONIZE=false

# === Redis Configuration ===
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password
REDIS_CACHE_ENABLED=true
REDIS_DB=0
REDIS_TTL=3600

# === SSO/Authentication Configuration ===
# Your SSO backend URL (e.g., https://your-sso-server.com)
ENTERPRISE_AUTH_URL=https://your-sso-server.com
ENTERPRISE_AUTH_URL_EXT=https://your-sso-server.com
ENTERPRISE_AUTH_JWT_SECRET=your_jwt_secret_minimum_32_characters

# Enable Enterprise SSO login button
ENABLE_ENTERPRISE_SSO=true
AUTH_URL=https://your-sso-server.com

# === Frontend Configuration ===
# Path to the built frontend (see Build Frontend section below)
FRONTEND_DIRECTORY=/path/to/openhands/frontend/build
# Your public domain
WEB_HOST=your-domain.com

# === Sandbox Configuration ===
RUNTIME=docker
SANDBOX_CONTAINER_URL_PATTERN=http://localhost:{port}
SANDBOX_STARTUP_GRACE_SECONDS=60
SANDBOX_MAX_NUM_SANDBOXES=1
AGENT_SERVER_USE_HOST_NETWORK=true

# === Analytics (Optional) ===
POSTHOG_CLIENT_KEY=your_posthog_key
```

### 2. Build Frontend

```bash
cd /path/to/openhands/frontend
npm install
npm run build
```

This creates the static files in `frontend/build/` directory.

### 3. Start Backend

```bash
cd /path/to/openhands/enterprise
make start-backend-3009
```

The backend will start on port 3009 and serve both the API and static frontend files.

### 4. Configure Apache Reverse Proxy

Add to your Apache site configuration (e.g., `/etc/apache2/sites-available/your-domain.conf`):

```apache
<VirtualHost *:443>
    ServerName your-domain.com
    ServerAdmin admin@your-domain.com
    
    # SSL Configuration
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/your-domain.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/your-domain.com/privkey.pem
    
    # Security Headers
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"
    Header always set X-Content-Type-Options nosniff
    Header always set X-Frame-Options DENY
    Header always set X-XSS-Protection "1; mode=block"
    
    # Enable proxy modules
    ProxyPreserveHost On
    
    # WebSocket support - MUST come before the main Location block
    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} websocket [NC]
    RewriteCond %{HTTP:Connection} upgrade [NC]
    RewriteRule ^/socket.io/(.*) ws://127.0.0.1:3009/socket.io/$1 [P,L,QSA]
    
    # Main proxy to OpenHands backend
    <Location />
        ProxyPass http://127.0.0.1:3009/
        ProxyPassReverse http://127.0.0.1:3009/
        RequestHeader set X-Forwarded-Proto "https"
        RequestHeader set X-Forwarded-Port "443"
    </Location>
</VirtualHost>
```

Enable required modules and restart Apache:

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers ssl
sudo apache2ctl configtest
sudo systemctl reload apache2
```

## Verify Installation

### 1. Test Backend API

```bash
curl http://127.0.0.1:3009/api/v1/web-client/config
```

Expected output:
```json
{
  "app_mode": "saas",
  "providers_configured": ["enterprise_sso"],
  "auth_url": "https://your-sso-server.com",
  "feature_flags": {...},
  ...
}
```

### 2. Access the Application

Open your browser and navigate to: `https://your-domain.com`

You should see:
1. The login page with "Enterprise SSO" button
2. Click the button to redirect to your SSO login page
3. After authentication, you'll be redirected back to OpenHands

## Troubleshooting

### Backend fails to start

Check if all required environment variables are set:
```bash
cd /path/to/openhands/enterprise
poetry run python -c "from server.config import get_config; print(get_config())"
```

### App mode shows "oss" instead of "saas"

Ensure `OH_APP_MODE=saas` is set in your `.env` file.

### SSO button not showing

Verify these environment variables are set:
```bash
grep -E "ENABLE_ENTERPRISE_SSO|AUTH_URL" enterprise/.env
```

Expected output:
```
ENABLE_ENTERPRISE_SSO=true
AUTH_URL=https://your-sso-server.com
```

### WebSocket connection issues

Ensure `proxy_wstunnel` module is enabled:
```bash
sudo a2enmod proxy_wstunnel
sudo systemctl restart apache2
```

### Check API configuration

```bash
curl -s http://127.0.0.1:3009/api/v1/web-client/config | python3 -c "import sys,json; d=json.load(sys.stdin); print('app_mode:', d.get('app_mode')); print('providers:', d.get('providers_configured'))"
```

## SSO Backend Requirements

Your SSO backend must support OAuth2 Authorization Code flow:

1. **Authorization Endpoint**: `GET /oauth/authorize`
   - Parameters: `client_id`, `redirect_uri`, `response_type=code`, `scope`, `state`

2. **Token Endpoint**: `POST /oauth/token`
   - Exchange authorization code for access token and refresh token

3. **User Info Endpoint**: `GET /userinfo` (optional)
   - Get user profile information

## Security Notes

1. **Never commit `.env` file** - it contains sensitive credentials
2. Use strong, random values for `ENTERPRISE_AUTH_JWT_SECRET`
3. Always use HTTPS in production
4. Keep your SSO backend credentials secure
5. Regularly rotate secrets and tokens
