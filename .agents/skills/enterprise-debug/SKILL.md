---
name: enterprise-debug
description: Debug and troubleshoot the OpenHands Enterprise deployment at https://ai.canthotouring.com/. Use this skill when the user mentions "enterprise debug", "debug enterprise", "ai.canthotouring.com issues", "enterprise authentication issues", "401 unauthorized", "page refresh loop", "rate limit 429", "PostgreSQL connection failed", or any issues related to the OpenHands enterprise deployment. This skill provides MCP Web tools for browser debugging, server management commands, and common troubleshooting scenarios.
---

# OpenHands Enterprise Debugging Skill

This skill provides comprehensive debugging and development instructions for the OpenHands Enterprise deployment at https://ai.canthotouring.com/

## Quick Debug Workflow

1. **Check if server is running**: `ps aux | grep uvicorn`
2. **Check server logs**: `tail -100 /tmp/enterprise-server.log`
3. **Use MCP Web tools** to verify the web UI is working
4. **Check database connection**: `docker exec openhands-postgres psql -U postgres -d openhands -c "SELECT 1;"`

## Using MCP Web Tools for Debugging

When debugging the web application, use the Playwright MCP tools to interact with the browser:

### Navigate to the Application
```
mcp--playwright--browser_navigate: url="https://ai.canthotouring.com/"
```

### Check Page State
```
mcp--playwright--browser_snapshot
```

### View Network Requests
```
mcp--playwright--browser_network_requests
```

### Check Console Messages
```
mcp--playwright--browser_console_messages
```

### Wait for Page Load
```
mcp--playwright--browser_wait_for: time=5
```

### Take Screenshot
```
mcp--playwright--browser_take_screenshot: filename="debug-screenshot.png"
```

### Click Elements
```
mcp--playwright--browser_click: element="Button description", ref="element_ref"
```

### Type Text
```
mcp--playwright--browser_type: element="Input field", ref="input_ref", text="text to type"
```

## Project Structure

```
/home/xdien/workspace/OpenHands/
├── enterprise/                    # Enterprise backend code
│   ├── .env                       # Enterprise environment configuration
│   ├── server/                    # Server code
│   │   ├── auth/                  # Authentication modules
│   │   │   └── saas_user_auth.py  # Token parsing, user auth logic
│   │   ├── middleware.py          # Auth middleware (email verification, etc.)
│   │   └── routes/                # API routes
│   └── storage/                   # Database models
├── frontend/                      # React frontend
│   ├── .env                       # Frontend environment configuration
│   └── src/                       # Source code
│       └── api/
│           └── open-hands-axios.ts # Axios interceptors (403 error handling)
├── openhands/                     # Core OpenHands code
└── .agents/skills/enterprise-debug/  # This skill
```

## Environment Configuration

### Enterprise Backend (`enterprise/.env`)
- **Backend Port**: 3009
- **Database**: PostgreSQL (localhost:5432, user: postgres, password: postgres, db: openhands)
- **Redis**: localhost:6379
- **Auth**: Enterprise custom backend at https://iot.canthotouring.com
- **Web Host**: ai.canthotouring.com
- **JWT Secret**: test_secret

### Frontend (`frontend/.env`)
- **Frontend Port**: 3001
- **Backend Host**: ai.canthotouring.com
- **TLS**: disabled (false)

## Quick Commands

### Start Enterprise Server
```bash
cd /home/xdien/workspace/OpenHands/enterprise && poetry run uvicorn enterprise.saas_server:app --port 3009 --host 0.0.0.0 &> /tmp/enterprise-server.log &
```

### Stop Enterprise Server
```bash
pkill -f "uvicorn enterprise.saas_server"
```

### View Server Logs
```bash
tail -100 /tmp/enterprise-server.log
```

### Reset PostgreSQL Password (if authentication fails)
```bash
docker exec openhands-postgres psql -U postgres -d postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';"
```

### Check Server Status
```bash
ps aux | grep uvicorn
```

## Common Debugging Scenarios

### 1. 401 Unauthorized After Login

**Symptoms**: User logs in successfully but API calls return 401

**Root Cause**: Enterprise JWT tokens use different fields than Keycloak tokens

**Files to Check**:
- `enterprise/server/auth/saas_user_auth.py` - Token parsing logic (line 353-358)
- `enterprise/server/middleware.py` - Auth middleware

**Debug Steps**:
1. Check if JWT token has required fields (`userId` or `sub`)
2. Verify token is being correctly decoded
3. Check if user_id is being extracted properly

**Fix Applied**: Support both `sub` and `userId` fields in token parsing:
```python
user_id = (
    access_token_payload.get('sub') or
    access_token_payload.get('userId') or
    access_token_payload.get('user_id') or
    access_token_payload.get('id')
)
```

### 2. Continuous Page Refresh / Rate Limit 429

**Symptoms**: Page keeps reloading, eventually shows rate limit error

**Root Cause**: `EmailNotVerifiedError` causing 403 response, triggering frontend reload loop

**Files to Check**:
- `enterprise/server/auth/saas_user_auth.py:369-372` - `email_verified` default value
- `enterprise/server/middleware.py:59-66` - Email verification check
- `frontend/src/api/open-hands-axios.ts:44-60` - 403 error handler

**Debug Steps**:
1. Use MCP Web tools to check network requests
2. Check server logs for 403 responses
3. Verify if `email_verified` field exists in JWT token

**Fix Applied**: Default `email_verified` to `True` for enterprise tokens:
```python
# For enterprise tokens without email_verified field, default to True
# since enterprise auth already validates users through their own system
email_verified = access_token_payload.get('email_verified', True)
```

### 3. PostgreSQL Connection Errors

**Symptoms**: `password authentication failed for user "postgres"`

**Root Cause**: PostgreSQL password not persisted after container restart

**Quick Fix**:
```bash
docker exec openhands-postgres psql -U postgres -d postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';"
```

**Permanent Fix**: Set POSTGRES_PASSWORD environment variable in docker-compose or use a volume for PostgreSQL data.

### 4. Database Connection Issues

**Check PostgreSQL Status**:
```bash
docker ps | grep postgres
docker logs openhands-postgres
```

**Test Connection**:
```bash
docker exec openhands-postgres psql -U postgres -d openhands -c "SELECT 1;"
```

### 5. Redis Connection Issues

**Check Redis Status**:
```bash
docker ps | grep redis
redis-cli -p 6379 ping
```

## Authentication Flow

```
1. User authenticates at https://iot.canthotouring.com
2. Enterprise backend returns JWT token
3. Frontend stores token in cookie (keycloak_auth)
4. Backend middleware validates token on each request
5. SaasUserAuth extracts user_id from token
```

### Key Authentication Files

| File | Purpose |
|------|---------|
| `enterprise/server/auth/saas_user_auth.py` | Token parsing, user auth logic |
| `enterprise/server/middleware.py` | Request middleware, email verification |
| `enterprise/server/auth/auth_error.py` | Auth error classes |
| `frontend/src/api/auth-service/` | Frontend auth API calls |

## Log Analysis

### Check for Auth Errors
```bash
grep -i "auth\|401\|403\|unauthorized" /tmp/enterprise-server.log | tail -50
```

### Check for Database Errors
```bash
grep -i "database\|postgres\|connection" /tmp/enterprise-server.log | tail -50
```

### Check for Rate Limiting
```bash
grep -i "rate\|limit\|429" /tmp/enterprise-server.log | tail -50
```

## Testing Authentication

### Decode JWT Token
```python
import jwt
token = "your_token_here"
decoded = jwt.decode(token, options={'verify_signature': False})
print(decoded)
```

### Test API Endpoint
```bash
curl -H "Cookie: keycloak_auth=your_token" https://ai.canthotouring.com/api/settings
```

## Pre-commit Checks

Before pushing changes, run:

### Backend Linting
```bash
cd /home/xdien/workspace/OpenHands/enterprise
poetry run pre-commit run --config ./dev_config/python/.pre-commit-config.yaml
```

### Frontend Linting
```bash
cd /home/xdien/workspace/OpenHands/frontend
npm run lint:fix && npm run build
```

## Useful Endpoints

| Endpoint | Description | Expected Status |
|----------|-------------|-----------------|
| `/api/v1/web-client/config` | Frontend configuration | 200 OK |
| `/api/settings` | User settings | 404 for new users |
| `/api/conversations?limit=10` | List conversations | 200 OK |
| `/api/options/models` | Available LLM models | 200 OK |
| `/api/options/agents` | Available agents | 200 OK |

## Troubleshooting Checklist

1. [ ] Is the enterprise server running? (`ps aux | grep uvicorn`)
2. [ ] Is PostgreSQL running? (`docker ps | grep postgres`)
3. [ ] Is Redis running? (`docker ps | grep redis`)
4. [ ] Check server logs for errors (`tail -100 /tmp/enterprise-server.log`)
5. [ ] Verify JWT token contains required fields
6. [ ] Check if email_verified is causing issues
7. [ ] Verify database password is correct
8. [ ] Use MCP Web tools to check frontend network requests
9. [ ] Check browser console for errors

## Related Documentation

- [Enterprise Auth Integration](docs/enterprise-auth-integration.md)
- [Discord Debug Guide](docs/discord-debug-guide.md)
- [AGENTS.md](AGENTS.md) - Main development guidelines
