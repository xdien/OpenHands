#!/bin/bash
# Start OpenHands SaaS Server with Discord Integration
# Usage: ./start-discord-server.sh

set -e

# Configuration
OPENHANDS_DIR="/home/xdien/workspace/OpenHands"
ENTERPRISE_DIR="${OPENHANDS_DIR}/enterprise"
FRONTEND_DIR="${OPENHANDS_DIR}/frontend/build"
SERVER_PORT=3000
REDIS_PORT=6381

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== OpenHands SaaS Server with Discord Integration ===${NC}"

# Check if already running on port 3000
if lsof -Pi :$SERVER_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Warning: Port $SERVER_PORT is already in use${NC}"
    echo "Checking if it's the OSS server..."
    if ps aux | grep -v grep | grep "openhands.server.listen" > /dev/null; then
        echo -e "${YELLOW}OSS server is running. Stopping it...${NC}"
        OSS_PID=$(ps aux | grep -v grep | grep "openhands.server.listen" | awk '{print $2}')
        kill $OSS_PID 2>/dev/null || true
        sleep 2
    elif ps aux | grep -v grep | grep "saas_server" > /dev/null; then
        echo -e "${GREEN}SaaS server is already running.${NC}"
        echo "Use 'stop-discord-server.sh' to stop it first."
        exit 0
    else
        echo -e "${RED}Another process is using port $SERVER_PORT${NC}"
        echo "Run: lsof -i :$SERVER_PORT to identify and stop it"
        exit 1
    fi
fi

# Load environment variables from frontend/.env if exists
if [ -f "${OPENHANDS_DIR}/frontend/.env" ]; then
    echo -e "${GREEN}Loading environment from frontend/.env...${NC}"
    # Export variables that match DISCORD* or POSTHOG* or REDIS*
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        # Only export relevant variables
        if [[ "$key" =~ ^DISCORD ]] || [[ "$key" =~ ^POSTHOG ]] || [[ "$key" =~ ^REDIS ]]; then
            # Remove quotes if present
            value="${value%\"}"
            value="${value#\"}"
            export "$key=$value"
            echo "  Exported: $key"
        fi
    done < "${OPENHANDS_DIR}/frontend/.env"
fi

# Set required environment variables
export DISCORD_WEBHOOKS_ENABLED="${DISCORD_WEBHOOKS_ENABLED:-true}"
export POSTHOG_CLIENT_KEY="${POSTHOG_CLIENT_KEY:-dummy_key_for_testing}"
export FRONTEND_DIRECTORY="${FRONTEND_DIR}"
export REDIS_HOST="${REDIS_HOST:-localhost}"
export REDIS_PORT="${REDIS_PORT:-6381}"

# Verify required Discord variables
MISSING_VARS=0
if [ -z "$DISCORD_BOT_TOKEN" ]; then
    echo -e "${RED}Error: DISCORD_BOT_TOKEN is not set${NC}"
    MISSING_VARS=1
fi
if [ -z "$DISCORD_PUBLIC_KEY" ]; then
    echo -e "${RED}Error: DISCORD_PUBLIC_KEY is not set${NC}"
    MISSING_VARS=1
fi
if [ -z "$DISCORD_CLIENT_ID" ]; then
    echo -e "${RED}Error: DISCORD_CLIENT_ID is not set${NC}"
    MISSING_VARS=1
fi

if [ $MISSING_VARS -eq 1 ]; then
    echo ""
    echo -e "${YELLOW}Please set the required Discord variables:${NC}"
    echo "  export DISCORD_BOT_TOKEN=your_token"
    echo "  export DISCORD_PUBLIC_KEY=your_public_key"
    echo "  export DISCORD_CLIENT_ID=your_client_id"
    echo ""
    echo "Or add them to ${OPENHANDS_DIR}/frontend/.env"
    exit 1
fi

# Check Redis connection
echo -e "${GREEN}Checking Redis connection...${NC}"
if ! docker ps | grep -q "redis"; then
    echo -e "${YELLOW}Redis container not found. Starting dev_redis...${NC}"
    if docker ps -a | grep -q "dev_redis"; then
        docker start dev_redis
    else
        docker run -d --name dev_redis -p ${REDIS_PORT}:6379 redis:latest
    fi
    sleep 2
fi

# Check frontend build
if [ ! -d "${FRONTEND_DIR}" ]; then
    echo -e "${YELLOW}Frontend build not found. Building...${NC}"
    cd "${OPENHANDS_DIR}/frontend"
    npm install
    npm run build
fi

# Start the server
echo -e "${GREEN}Starting SaaS server...${NC}"
cd "${ENTERPRISE_DIR}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Server Configuration:${NC}"
echo "  - Port: $SERVER_PORT"
echo "  - Discord Webhooks: $DISCORD_WEBHOOKS_ENABLED"
echo "  - Redis: $REDIS_HOST:$REDIS_PORT"
echo "  - Frontend: $FRONTEND_DIRECTORY"
echo ""
echo -e "${GREEN}Discord Interactions Endpoint URL:${NC}"
echo "  https://canthotouring.com/discord/on-event"
echo ""
echo -e "${GREEN}Health Check:${NC}"
echo "  curl http://localhost:$SERVER_PORT/discord/health"
echo -e "${GREEN}========================================${NC}"
echo ""

# Run the server
poetry run uvicorn saas_server:app --host 0.0.0.0 --port $SERVER_PORT
