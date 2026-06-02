#!/bin/bash
# Test script for proxy URL pattern configuration

echo "=== Testing Proxy URL Pattern Configuration ==="
echo

# Test 1: Verify environment variables
echo "1. Checking environment variables..."
if [ -z "$SANDBOX_CONTAINER_URL_PATTERN" ]; then
    echo "❌ SANDBOX_CONTAINER_URL_PATTERN not set"
    echo "   Set it to: http://localhost:{port}"
else
    echo "✅ SANDBOX_CONTAINER_URL_PATTERN=$SANDBOX_CONTAINER_URL_PATTERN"
fi

if [ -z "$SANDBOX_PROXY_URL_PATTERN" ]; then
    echo "⚠️  SANDBOX_PROXY_URL_PATTERN not set (optional)"
    echo "   Set it to: https://domain.com/ws/:{port} for proxy mode"
else
    echo "✅ SANDBOX_PROXY_URL_PATTERN=$SANDBOX_PROXY_URL_PATTERN"
fi
echo

# Test 2: Simulate backend URL building
echo "2. Testing URL building logic..."
python3 << 'PYTHONSCRIPT'
import os

container_url_pattern = os.getenv('SANDBOX_CONTAINER_URL_PATTERN', 'http://localhost:{port}')
proxy_url_pattern = os.getenv('SANDBOX_PROXY_URL_PATTERN')

port = 36375

# Internal URL (for health check)
internal_url = container_url_pattern.format(port=port)
print(f"Internal URL (health check): {internal_url}")

# External URL (for frontend)
if proxy_url_pattern:
    external_url = proxy_url_pattern.format(port=port)
    print(f"External URL (frontend): {external_url}")
else:
    external_url = internal_url
    print(f"External URL (frontend): {external_url} (fallback to internal)")

# Test URL parsing
import sys
try:
    from urllib.parse import urlparse
    parsed_internal = urlparse(internal_url)
    parsed_external = urlparse(external_url)

    print(f"\nInternal URL parsing:")
    print(f"  - Scheme: {parsed_internal.scheme}")
    print(f"  - Host: {parsed_internal.netloc}")
    print(f"  - Port: {parsed_internal.port or 'default'}")

    print(f"\nExternal URL parsing:")
    print(f"  - Scheme: {parsed_external.scheme}")
    print(f"  - Host: {parsed_external.netloc}")
    print(f"  - Port: {parsed_external.port or 'default'}")
    print(f"  - Path: {parsed_external.path}")

    # Check if external URL has proxy path
    if proxy_url_pattern and '/ws/:' in external_url:
        print(f"\n✅ Proxy mode detected: path contains '/ws/:{port}'")
    elif parsed_external.port and parsed_external.port > 30000:
        print(f"\n✅ Direct port mode: port {parsed_external.port}")

except Exception as e:
    print(f"❌ Error parsing URLs: {e}")
    sys.exit(1)
PYTHONSCRIPT

echo

# Test 3: Check Nginx configuration (optional)
echo "3. Checking Nginx configuration (if installed)..."
if [ -f "/etc/nginx/sites-enabled/openhands.conf" ] || [ -f "/etc/nginx/conf.d/openhands.conf" ]; then
    echo "✅ Nginx configuration found"

    # Check if proxy routing is configured
    if grep -q "ws/:" /etc/nginx/sites-enabled/openhands.conf 2>/dev/null || \
       grep -q "ws/:" /etc/nginx/conf.d/openhands.conf 2>/dev/null; then
        echo "✅ Proxy routing pattern found in Nginx config"
    else
        echo "⚠️  Proxy routing pattern not found in Nginx config"
        echo "   Add this to your Nginx config:"
        echo '   location ~ ^/ws/:(?<sandbox_port>\d+)/(.*)$ {'
        echo '       proxy_pass http://localhost:$sandbox_port/$2;'
        echo '   }'
    fi
else
    echo "⚠️  Nginx configuration not found (skip if using another proxy)"
fi
echo

# Test 4: Health check simulation
echo "4. Testing health check URL..."
python3 << 'PYTHONSCRIPT'
import os
import urllib.parse

container_url_pattern = os.getenv('SANDBOX_CONTAINER_URL_PATTERN', 'http://localhost:{port}')
port = 36375

internal_url = container_url_pattern.format(port=port)
health_check_url = internal_url + '/health'

print(f"Health check URL: {health_check_url}")
print(f"Expected format: http://localhost:{port}/health")

parsed = urllib.parse.urlparse(health_check_url)
if parsed.scheme == 'http' and parsed.port == port:
    print(f"✅ Health check URL format is correct")
else:
    print(f"❌ Health check URL format is incorrect")
PYTHONSCRIPT

echo

# Summary
echo "=== Configuration Summary ==="
if [ -n "$SANDBOX_PROXY_URL_PATTERN" ]; then
    echo "Mode: PROXY (with external URL pattern)"
    echo "  - Internal URL: http://localhost:{port} (for health checks)"
    echo "  - External URL: $SANDBOX_PROXY_URL_PATTERN (for frontend)"
    echo
    echo "Next steps:"
    echo "  1. Configure Nginx/Traefik with proxy routing"
    echo "  2. Restart OpenHands application"
    echo "  3. Test WebSocket connection through proxy"
else
    echo "Mode: DIRECT PORT (fallback)"
    echo "  - Both internal and external use: $SANDBOX_CONTAINER_URL_PATTERN"
    echo
    echo "To enable proxy mode, set:"
    echo "  export SANDBOX_PROXY_URL_PATTERN=https://your-domain.com/ws/:{port}"
fi

echo
echo "=== Configuration Test Complete ==="
