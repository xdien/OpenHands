#!/bin/bash
set -e

# Source the configuration
if [ -f .env.keycloak ]; then
    export $(grep -v '^#' .env.keycloak | xargs)
fi

# Set defaults if not set
export KEYCLOAK_REALM_NAME=${KEYCLOAK_REALM_NAME:-allhands}
export KEYCLOAK_CLIENT_ID=${KEYCLOAK_CLIENT_ID:-openhands}
export KEYCLOAK_CLIENT_SECRET=${KEYCLOAK_CLIENT_SECRET:-openhands-secret}
export WEB_HOST=${WEB_HOST:-localhost}
export AUTH_WEB_HOST=${AUTH_WEB_HOST:-localhost:8080}
export DISCORD_CLIENT_ID=${DISCORD_CLIENT_ID:-}
export DISCORD_CLIENT_SECRET=${DISCORD_CLIENT_SECRET:-}

echo "Generating Keycloak realm configuration..."
envsubst < enterprise/allhands-realm-github-provider.json.tmpl > keycloak-realm.json

echo "Keycloak realm configuration generated in keycloak-realm.json"
echo "You can now start Keycloak using:"
echo "docker-compose -f docker-compose.keycloak.yml up -d"
