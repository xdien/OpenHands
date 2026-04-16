import os
from urllib.parse import quote

import redis

# Redis configuration
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_USERNAME = os.environ.get("REDIS_USERNAME", "")


def create_redis_client():
    """Create a Redis client with optional username support for Redis 6+ ACL."""
    client_kwargs = {
        "host": REDIS_HOST,
        "port": REDIS_PORT,
        "password": REDIS_PASSWORD or None,
        "db": REDIS_DB,
        "socket_timeout": 2,
    }
    # Add username if provided (Redis 6+ ACL support)
    if REDIS_USERNAME:
        client_kwargs["username"] = REDIS_USERNAME
    return redis.Redis(**client_kwargs)


def get_redis_authed_url():
    """Get Redis authenticated URL with optional username support."""
    # URL-encode the password to handle special characters like #, @, etc.
    encoded_password = quote(REDIS_PASSWORD, safe="")
    # Support Redis 6+ ACL with username
    if REDIS_USERNAME:
        encoded_username = quote(REDIS_USERNAME, safe="")
        return f"redis://{encoded_username}:{encoded_password}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    return f"redis://:{encoded_password}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
