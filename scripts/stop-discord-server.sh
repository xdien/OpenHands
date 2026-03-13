#!/bin/bash
# Stop OpenHands SaaS Server
# Usage: ./stop-discord-server.sh

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Stopping OpenHands SaaS Server ===${NC}"

# Find and kill SaaS server
if ps aux | grep -v grep | grep "saas_server" > /dev/null; then
    SAAS_PID=$(ps aux | grep -v grep | grep "saas_server" | awk '{print $2}')
    echo -e "${YELLOW}Stopping SaaS server (PID: $SAAS_PID)...${NC}"
    kill $SAAS_PID 2>/dev/null || true
    sleep 2

    # Force kill if still running
    if ps -p $SAAS_PID > /dev/null 2>&1; then
        echo -e "${YELLOW}Force killing...${NC}"
        kill -9 $SAAS_PID 2>/dev/null || true
    fi

    echo -e "${GREEN}SaaS server stopped.${NC}"
else
    echo -e "${YELLOW}SaaS server is not running.${NC}"
fi

# Also check for OSS server on port 3000
if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Found process on port 3000${NC}"
    if ps aux | grep -v grep | grep "openhands.server.listen" > /dev/null; then
        OSS_PID=$(ps aux | grep -v grep | grep "openhands.server.listen" | awk '{print $2}')
        echo -e "${YELLOW}Stopping OSS server (PID: $OSS_PID)...${NC}"
        kill $OSS_PID 2>/dev/null || true
        echo -e "${GREEN}OSS server stopped.${NC}"
    fi
fi

# Verify port is free
if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${RED}Port 3000 is still in use. Check manually:${NC}"
    lsof -i :3000
else
    echo -e "${GREEN}Port 3000 is now free.${NC}"
fi
