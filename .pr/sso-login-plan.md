# SSO Login Implementation Plan for OpenHands Enterprise

## Overview

This document describes the Single Sign-On (SSO) authentication flow for OpenHands Enterprise using a custom backend (currently at `iot.canthotouring.com`).

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User Browser                                     │
│                                                                          │
│   https://ai.canthotouring.com ──────► Apache Reverse Proxy (443)       │
└─────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ Proxy to backend
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    OpenHands Enterprise Backend                         │
│                         (127.0.0.1:3009)                                │
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │   Frontend   │    │     API      │    │  WebSocket  │               │
│  │  (Static)   │    │  /api/*      │    │ /socket.io/ │               │
│  └──────────────┘    └──────────────┘    └──────────────┘               │
│                              │                                           │
│                              ▼                                           │
│                    ┌────────────────┐                                    │
│                    │  Auth Middleware│◄─── NoCredentialsError          │
│                    └────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ Redirect to SSO
                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              Custom SSO Backend (iot.canthotouring.com)                 │
│                                                                          │
│   - Keycloak or custom auth service                                     │
│   - OAuth2/OIDC provider                                                │
│   - Returns JWT tokens                                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Authentication Flow

### 1. Initial Request Flow

```
User Browser
     │
     │ 1. GET https://ai.canthotouring.com
     ▼
Apache Reverse Proxy
     │
     │ 2. Proxy to http://127.0.0.1:3009
     ▼
OpenHands Backend (port 3009)
     │
     │ 3. Return static frontend (index.html + assets)
     ▼
User Browser receives HTML/JS/CSS
     │
     │ 4. Browser loads React app
     ▼
React App calls /api/v1/web-client/config
     │  (to get app_mode, auth_url, providers_configured)
     ▼
Backend returns config with:
     {
       "app_mode": "saas",
       "auth_url": "https://iot.canthotouring.com",
       "providers_configured": ["enterprise_sso"]
     }
```

### 2. Login Flow (Enterprise SSO)

```
React Login Page
     │
     │ 1. User clicks "Connect with Enterprise SSO"
     ▼
Browser redirects to:
  https://iot.canthotouring.com/oauth/authorize?
    client_id=openhands&
    redirect_uri=https://ai.canthotouring.com/api/auth/callback&
    response_type=code&
    scope=openid%20profile%20email&
    state=xyz123
     │
     ▼
Custom SSO Backend (iot.canthotouring.com)
     │
     │ 2. If not logged in, show login page
     │    User enters credentials
     │    (or use existing session)
     ▼
     │ 3. Validate credentials
     │    Create session / issue JWT
     ▼
     │ 4. Redirect back with auth code
     │
     │ 302: https://ai.canthotouring.com/api/auth/callback?code=xxx&state=xyz123
     ▼
Browser follows redirect to OpenHands Backend
     │
     │ 5. POST /api/auth/callback
     │    (exchange code for tokens)
     ▼
OpenHands Backend
     │
     │ 6. Call SSO backend to exchange code for tokens
     │    POST https://iot.canthotouring.com/oauth/token
     │
     │ 7. Receive tokens:
     │    - access_token
     │    - refresh_token
     │    - id_token (contains user info)
     ▼
     │ 8. Create/update user in database
     │    Store refresh_token securely
     ▼
     │ 9. Set auth cookie (keycloak_auth)
     │    Redirect to home page
     ▼
User is logged in!
```

### 3. Subsequent Requests (Authenticated)

```
User Browser
     │
     │ 1. GET /api/v1/settings
     │    Cookie: keycloak_auth=<signed_jwt>
     ▼
OpenHands Backend
     │
     │ 2. Auth Middleware extracts cookie
     │ 3. Verify JWT signature
     │ 4. Get user info from token
     │ 5. Check rate limits
     ▼
     │ 6. Process request
     ▼
Return data + 200 OK
```

### 4. Token Refresh Flow

```
User Browser
     │
     │ 1. API call with expired access_token
     │    OR 401 Unauthorized received
     ▼
OpenHands Backend
     │
     │ 2. Detect expired/invalid token
     │ 3. Get refresh_token from database
     │ 4. Call SSO backend to refresh:
     │    POST https://iot.canthotouring.com/oauth/refresh
     │    body: { refresh_token: xxx }
     ▼
     │ 5. Get new tokens
     ▼
     │ 6. Update stored tokens
     │ 7. Retry original request
     ▼
Return data + 200 OK
```

### 5. Logout Flow

```
User Browser
     │
     │ 1. POST /api/logout
     ▼
OpenHands Backend
     │
     │ 2. Delete stored refresh_token
     │ 3. Clear auth cookie
     │ 4. Optionally redirect to SSO logout
     ▼
Redirect to login page
```

## Implementation Components

### Backend Files to Modify/Create

| File | Description |
|------|-------------|
| `enterprise/server/routes/auth.py` | OAuth callback endpoint |
| `enterprise/server/auth/saas_user_auth.py` | User authentication logic |
| `enterprise/server/auth/enterprise_auth.py` | (NEW) Enterprise SSO client |
| `enterprise/server/middleware.py` | Auth middleware |

### Environment Variables Required

| Variable | Description | Example |
|----------|-------------|---------|
| `ENTERPRISE_AUTH_URL` | SSO server base URL | `https://iot.canthotouring.com` |
| `ENTERPRISE_AUTH_URL_EXT` | External SSO URL | `https://iot.canthotouring.com` |
| `ENTERPRISE_AUTH_JWT_SECRET` | JWT signing secret | (secure random string) |
| `ENABLE_ENTERPRISE_SSO` | Enable SSO | `true` |

### Optional (if using OAuth2)

| Variable | Description | Example |
|----------|-------------|---------|
| `ENTERPRISE_CLIENT_ID` | OAuth2 client ID | `openhands` |
| `ENTERPRISE_CLIENT_SECRET` | OAuth2 client secret | (secure random string) |

## API Endpoints

### New/Modified Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/authenticate` | POST | Check if user is authenticated |
| `/api/auth/callback` | GET | OAuth2 callback handler |
| `/api/logout` | POST | Logout user |
| `/api/v1/users/me` | GET | Get current user info |
| `/api/v1/settings` | GET/POST | User settings |

## Database Schema (if needed)

### Users Table

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255),
    avatar_url TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Auth Tokens Table

```sql
CREATE TABLE auth_tokens (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    refresh_token_encrypted TEXT NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## Security Considerations

1. **Token Storage**:
   - Access tokens: In-memory only (short-lived)
   - Refresh tokens: Encrypted in database
   - JWT: Signed with `ENTERPRISE_AUTH_JWT_SECRET`

2. **Cookie Security**:
   - `HttpOnly`: Yes (prevent XSS)
   - `Secure`: Yes (HTTPS only)
   - `SameSite`: `lax` or `strict`
   - `Max-Age`: Session or short duration

3. **Rate Limiting**:
   - Apply to `/api/auth/*` endpoints
   - Use Redis for distributed rate limiting

4. **HTTPS**:
   - All traffic must be over HTTPS
   - HSTS header enabled

## Testing Checklist

- [ ] Initial page load works (no auth)
- [ ] Redirect to SSO login page
- [ ] Successful login with valid credentials
- [ ] Failed login shows error
- [ ] Logout clears session
- [ ] Token refresh works
- [ ] API calls with valid token succeed
- [ ] API calls without token return 401
- [ ] Rate limiting works

## Migration from Current Implementation

The current implementation already has:
- `ENTERPRISE_AUTH_URL` configured
- `ENABLE_ENTERPRISE_SSO=true`
- Basic auth redirect logic

Missing components:
- [ ] OAuth2 callback handler at `/api/auth/callback`
- [ ] Token exchange logic
- [ ] User creation/sync
- [ ] Refresh token management
- [ ] Logout endpoint

## Next Steps

1. Verify SSO backend (`iot.canthotouring.com`) is accessible
2. Get OAuth2 credentials (client_id, client_secret) from SSO admin
3. Implement OAuth callback endpoint
4. Test full flow
5. Deploy to production