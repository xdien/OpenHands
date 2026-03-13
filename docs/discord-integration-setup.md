# Discord Integration Setup Guide

This guide explains how to configure and run the OpenHands Discord integration on your server.

## Prerequisites

- OpenHands repository cloned to `/home/xdien/workspace/OpenHands`
- Docker installed (for Redis)
- Apache2 with mod_proxy, mod_ssl enabled
- Python 3.12
- Poetry installed
- Domain with SSL certificate (e.g., `canthotouring.com`)

## Architecture Overview

```
Discord API
    ↓ HTTPS Webhook
Cloudflare (optional)
    ↓
Apache2 Reverse Proxy (port 443)
    ↓ HTTP Proxy
OpenHands SaaS Server (port 3000)
    ↓
Redis (Docker, port 6381)
```

## Step 1: Discord Application Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application or select existing one
3. Go to **Bot** section and create a bot
4. Copy the following values:
   - **Application ID** (Client ID)
   - **Bot Token**
   - **Public Key** (from Application → General Information)

5. Enable required intents in Bot settings:
   - Message Content Intent
   - Server Members Intent (optional)

## Step 2: Environment Variables

Create environment variables for the SaaS server. You can add these to your `~/.bashrc` or create a startup script:

```bash
# Discord Configuration
export DISCORD_BOT_TOKEN=your_bot_token_here
export DISCORD_PUBLIC_KEY=your_public_key_here
export DISCORD_CLIENT_ID=your_client_id_here
export DISCORD_WEBHOOKS_ENABLED=true

# Server Configuration
export POSTHOG_CLIENT_KEY=dummy_key_for_testing
export FRONTEND_DIRECTORY=/home/xdien/workspace/OpenHands/frontend/build

# Redis Configuration (if using custom port)
export REDIS_HOST=localhost
export REDIS_PORT=6381
```

## Step 3: Apache Configuration

Edit your Apache virtual host configuration:

```bash
sudo nano /etc/apache2/sites-available/canthotouring.com.conf
```

Add the Discord proxy configuration inside the `<VirtualHost *:443>` section:

```apache
<VirtualHost *:443>
    ServerName canthotouring.com
    ServerAlias www.canthotouring.com
    ServerAdmin your-email@example.com
    DocumentRoot /var/www/html/canthotouring.com

    # Discord Integration - Webhook endpoint
    <Location /discord>
        ProxyPass http://127.0.0.1:3000/discord
        ProxyPassReverse http://127.0.0.1:3000/discord
        RequestHeader set X-Forwarded-Proto "https"
        RequestHeader set X-Forwarded-Port "443"
    </Location>

    # ... other configurations ...

    # SSL Configuration
    Include /etc/letsencrypt/options-ssl-apache.conf
    SSLCertificateFile /etc/letsencrypt/live/canthotouring.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/canthotouring.com/privkey.pem
</VirtualHost>
```

Enable required Apache modules:

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel ssl headers
sudo systemctl reload apache2
```

## Step 4: Redis Setup (Docker)

Redis is required for the SaaS server. Run it using Docker:

```bash
# Start Redis container
docker run -d \
    --name dev_redis \
    -p 6381:6379 \
    redis:latest

# Verify Redis is running
docker ps | grep redis
```

## Step 5: Install Enterprise Dependencies

```bash
cd /home/xdien/workspace/OpenHands/enterprise
poetry install
```

This will install all required dependencies including uvicorn.

## Step 6: Build Frontend

```bash
cd /home/xdien/workspace/OpenHands/frontend
npm install
npm run build
```

## Step 7: Start the SaaS Server

### Option A: Manual Start

```bash
cd /home/xdien/workspace/OpenHands/enterprise

# Set environment variables
export DISCORD_BOT_TOKEN=your_bot_token_here
export DISCORD_PUBLIC_KEY=your_public_key_here
export DISCORD_CLIENT_ID=your_client_id_here
export DISCORD_WEBHOOKS_ENABLED=true
export POSTHOG_CLIENT_KEY=dummy_key_for_testing
export FRONTEND_DIRECTORY=/home/xdien/workspace/OpenHands/frontend/build
export REDIS_HOST=localhost
export REDIS_PORT=6381

# Start server
poetry run uvicorn saas_server:app --host 0.0.0.0 --port 3000
```

### Option B: Using systemd Service (Recommended for Production)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/openhands-saas.service
```

Content:

```ini
[Unit]
Description=OpenHands SaaS Server with Discord Integration
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=xdien
Group=xdien
WorkingDirectory=/home/xdien/workspace/OpenHands/enterprise
Environment="DISCORD_BOT_TOKEN=your_bot_token_here"
Environment="DISCORD_PUBLIC_KEY=your_public_key_here"
Environment="DISCORD_CLIENT_ID=your_client_id_here"
Environment="DISCORD_WEBHOOKS_ENABLED=true"
Environment="POSTHOG_CLIENT_KEY=dummy_key_for_testing"
Environment="FRONTEND_DIRECTORY=/home/xdien/workspace/OpenHands/frontend/build"
Environment="REDIS_HOST=localhost"
Environment="REDIS_PORT=6381"
ExecStart=/home/xdien/.cache/pypoetry/virtualenvs/enterprise-server-*/bin/uvicorn saas_server:app --host 0.0.0.0 --port 3000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable openhands-saas
sudo systemctl start openhands-saas
sudo systemctl status openhands-saas
```

## Step 8: Configure Discord Interactions Endpoint URL

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application
3. Go to **Installation** (or **General Information**)
4. Enter the Interactions Endpoint URL:
   ```
   https://canthotouring.com/discord/on-event
   ```
5. Click **Save**

Discord will verify the endpoint by sending a PING request. If successful, you'll see a green checkmark.

## Step 9: Verify Installation

### Check Server Health

```bash
# Local check
curl http://localhost:3000/saas
# Expected: {"saas": true}

curl http://localhost:3000/discord/health
# Expected: {"status":"healthy","webhooks_enabled":true,"bot_configured":true}

# Remote check (via Apache)
curl https://canthotouring.com/discord/health
# Expected: {"status":"healthy","webhooks_enabled":true,"bot_configured":true}
```

### Test Discord PING

```bash
curl -X POST https://canthotouring.com/discord/on-event \
  -H "Content-Type: application/json" \
  -d '{"type": 1}'
# Expected: {"type": 1}
```

## Troubleshooting

### Common Issues

#### 1. "Method Not Allowed" Error

- **Cause**: OSS server is running instead of SaaS server
- **Solution**: Stop the OSS server and start the SaaS server
  ```bash
  # Find and kill OSS server
  ps aux | grep "openhands.server.listen"
  kill <pid>

  # Start SaaS server
  cd /home/xdien/workspace/OpenHands/enterprise
  poetry run uvicorn saas_server:app --host 0.0.0.0 --port 3000
  ```

#### 2. Redis Connection Error

- **Cause**: Redis is not running or wrong port
- **Solution**: Start Redis container
  ```bash
  docker start dev_redis
  # Or create new one
  docker run -d --name dev_redis -p 6381:6379 redis:latest
  ```

#### 3. "Missing posthog client key" Error

- **Cause**: POSTHOG_CLIENT_KEY not set
- **Solution**: Set the environment variable
  ```bash
  export POSTHOG_CLIENT_KEY=dummy_key_for_testing
  ```

#### 4. Signature Verification Failed

- **Cause**: Wrong DISCORD_PUBLIC_KEY
- **Solution**: Verify the public key matches your Discord application
  - Go to Discord Developer Portal → Your App → General Information
  - Copy the exact "Public Key" value

#### 5. Apache 502/503 Error

- **Cause**: Backend server not running
- **Solution**: Verify the SaaS server is running on port 3000
  ```bash
  netstat -tlnp | grep 3000
  ```

### Logs

Check server logs:

```bash
# If running manually, logs appear in terminal

# If running as systemd service
sudo journalctl -u openhands-saas -f

# Apache logs
tail -f /var/log/apache2/error.log
```

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes | Bot token from Discord Developer Portal |
| `DISCORD_PUBLIC_KEY` | Yes | Public key for signature verification |
| `DISCORD_CLIENT_ID` | Yes | Application/Client ID |
| `DISCORD_WEBHOOKS_ENABLED` | Yes | Set to `true` to enable webhooks |
| `POSTHOG_CLIENT_KEY` | Yes | PostHog analytics key (can be dummy for dev) |
| `FRONTEND_DIRECTORY` | Yes | Path to frontend build directory |
| `REDIS_HOST` | No | Redis host (default: localhost) |
| `REDIS_PORT` | No | Redis port (default: 6379) |

## Discord Bot Permissions

Required OAuth2 scopes and permissions:

**Scopes:**
- `bot`
- `applications.commands`

**Bot Permissions:**
- Send Messages
- Read Message History
- Mention Everyone
- Use Slash Commands

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/discord/on-event` | POST | Main webhook endpoint for Discord interactions |
| `/discord/health` | GET | Health check endpoint |
| `/discord/install` | GET | OAuth installation redirect |
| `/discord/install-callback` | GET | OAuth callback handler |

## Security Considerations

1. **Signature Verification**: All Discord webhooks are verified using Ed25519 signatures
2. **HTTPS Required**: Discord requires HTTPS for the Interactions Endpoint URL
3. **Keep Secrets Safe**: Never commit bot tokens or public keys to version control
4. **Rate Limiting**: Consider adding rate limiting for production use

## Support

For issues or questions:
- OpenHands Documentation: [docs/](docs/)
- Discord Integration Code: [enterprise/server/routes/integration/discord.py](enterprise/server/routes/integration/discord.py)
- Discord Manager: [enterprise/integrations/discord/](enterprise/integrations/discord/)
