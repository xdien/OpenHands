"""Enterprise Authentication Client for OpenHands.

This module provides a client for integrating with a custom enterprise backend
that handles authentication instead of Keycloak.

The enterprise backend should provide:
- POST /auth/login - Login with credentials, returns JWT tokens
- POST /auth/register - Register new user
- POST /auth/refresh - Refresh access token (optional)
- GET /auth/userinfo - Get user info from JWT
- POST /auth/verify - Verify JWT token (optional)

Expected JWT payload:
{
    "sub": "user-uuid",           # REQUIRED - User ID
    "email": "user@example.com",  # User email
    "email_verified": true,       # Email verification status
    "preferred_username": "username",  # Username
    "exp": 1234567890,            # Expiration timestamp
    "iat": 1234567800             # Issued at timestamp
}
"""

from typing import Optional
import httpx
import jwt
from pydantic import BaseModel
from server.logger import logger
from server.config import get_config
from server.auth.constants import (
    ENTERPRISE_AUTH_URL,
    ENTERPRISE_AUTH_URL_EXT,
    ENTERPRISE_AUTH_JWT_SECRET,
    ENTERPRISE_AUTH_JWT_PUBLIC_KEY,
)

# HTTP timeout for enterprise backend calls (in seconds)
ENTERPRISE_AUTH_HTTP_TIMEOUT = 15.0


class EnterpriseUserInfo(BaseModel):
    """Pydantic model for Enterprise UserInfo response.

    Compatible with KeycloakUserInfo for seamless integration.
    """
    model_config = {'extra': 'allow'}

    sub: str
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    preferred_username: str | None = None
    email: str | None = None
    email_verified: bool | None = None
    picture: str | None = None
    attributes: dict[str, list[str]] | None = None
    company: str | None = None
    roles: list[str] | None = None


class EnterpriseAuthClient:
    """Client for custom enterprise authentication backend.

    Provides methods for:
    - Exchanging authorization code for tokens (OAuth2 flow)
    - Login with credentials
    - Getting user info from JWT
    - Refreshing tokens
    - Verifying tokens
    """

    def __init__(self, external: bool = False):
        """Initialize the Enterprise auth client.

        Args:
            external: If True, use external URL for browser-facing operations
        """
        self.external = external
        self.base_url = ENTERPRISE_AUTH_URL_EXT if external else ENTERPRISE_AUTH_URL
        self.jwt_secret = ENTERPRISE_AUTH_JWT_SECRET or get_config().jwt_secret.get_secret_value()
        self.jwt_public_key = ENTERPRISE_AUTH_JWT_PUBLIC_KEY

    def _get_headers(self, access_token: str | None = None) -> dict:
        """Get headers for HTTP requests."""
        headers = {'Content-Type': 'application/json'}
        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
        return headers

    async def get_tokens_from_code(
        self, code: str, redirect_uri: str
    ) -> tuple[str | None, str | None]:
        """Exchange authorization code for JWT tokens.

        This is used in OAuth2 flow where the user has already authenticated
        with the enterprise backend and we receive an authorization code.

        Args:
            code: Authorization code from OAuth callback
            redirect_uri: The redirect URI used in the OAuth request

        Returns:
            Tuple of (access_token, refresh_token) or (None, None) on failure
        """
        if not self.base_url:
            logger.error('Enterprise auth backend URL not configured')
            return None, None

        try:
            async with httpx.AsyncClient(timeout=ENTERPRISE_AUTH_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f'{self.base_url}/auth/token',
                    json={
                        'code': code,
                        'redirect_uri': redirect_uri,
                        'grant_type': 'authorization_code',
                    },
                    headers=self._get_headers(),
                )

                if response.status_code != 200:
                    logger.error(f'Enterprise auth token exchange failed: {response.status_code} - {response.text}')
                    return None, None

                data = response.json()
                access_token = data.get('access_token')
                refresh_token = data.get('refresh_token')

                if not access_token:
                    logger.error('No access_token in enterprise auth response')
                    return None, None

                logger.debug('Successfully obtained tokens from enterprise auth')
                return access_token, refresh_token

        except httpx.TimeoutException:
            logger.error('Timeout when calling enterprise auth backend')
            return None, None
        except Exception as e:
            logger.exception(f'Exception when getting enterprise auth tokens: {e}')
            return None, None

    async def login(
        self, username: str, password: str
    ) -> tuple[str | None, str | None]:
        """Login with credentials.

        Args:
            username: Username or email
            password: Password

        Returns:
            Tuple of (access_token, refresh_token) or (None, None) on failure
        """
        if not self.base_url:
            logger.error('Enterprise auth backend URL not configured')
            return None, None

        try:
            async with httpx.AsyncClient(timeout=ENTERPRISE_AUTH_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f'{self.base_url}/auth/login',
                    json={
                        'username': username,
                        'password': password,
                    },
                    headers=self._get_headers(),
                )

                if response.status_code != 200:
                    logger.error(f'Enterprise auth login failed: {response.status_code} - {response.text}')
                    return None, None

                data = response.json()
                access_token = data.get('access_token')
                refresh_token = data.get('refresh_token')

                if not access_token:
                    logger.error('No access_token in enterprise auth login response')
                    return None, None

                logger.debug('Successfully logged in to enterprise auth')
                return access_token, refresh_token

        except Exception as e:
            logger.exception(f'Exception when logging in to enterprise auth: {e}')
            return None, None

    async def get_user_info(self, access_token: str) -> EnterpriseUserInfo | None:
        """Get user info from JWT token.

        Decodes the JWT locally to extract user info. Optionally verifies
        with the backend if /auth/userinfo endpoint is available.

        Args:
            access_token: JWT access token

        Returns:
            EnterpriseUserInfo or None if invalid
        """
        try:
            # First, decode locally (fast, no network call)
            payload = self.decode_jwt(access_token)

            if not payload:
                return None

            # Map JWT claims to EnterpriseUserInfo
            user_info = EnterpriseUserInfo(
                sub=payload.get('sub', ''),
                email=payload.get('email'),
                email_verified=payload.get('email_verified', False),
                preferred_username=payload.get('preferred_username') or payload.get('username'),
                name=payload.get('name'),
                given_name=payload.get('given_name'),
                family_name=payload.get('family_name'),
                picture=payload.get('picture'),
                roles=payload.get('roles', []),
            )

            return user_info

        except Exception as e:
            logger.exception(f'Exception when getting user info: {e}')
            return None

    def decode_jwt(self, token: str, verify: bool = True) -> dict | None:
        """Decode a JWT token.

        Args:
            token: JWT token string
            verify: Whether to verify signature (default True)

        Returns:
            Decoded payload or None if invalid
        """
        try:
            if verify and self.jwt_public_key:
                # Verify with public key (RS256)
                payload = jwt.decode(
                    token,
                    self.jwt_public_key,
                    algorithms=['RS256'],
                    options={'verify_aud': False},
                )
            elif verify and self.jwt_secret:
                # Verify with secret (HS256)
                payload = jwt.decode(
                    token,
                    self.jwt_secret,
                    algorithms=['HS256'],
                    options={'verify_aud': False},
                )
            else:
                # Skip verification (for development or when we just got the token)
                payload = jwt.decode(
                    token,
                    options={'verify_signature': False},
                )

            return payload

        except jwt.ExpiredSignatureError:
            logger.warning('JWT token has expired')
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f'Invalid JWT token: {e}')
            return None
        except Exception as e:
            logger.exception(f'Exception when decoding JWT: {e}')
            return None

    async def refresh_token(self, refresh_token: str) -> tuple[str | None, str | None]:
        """Refresh access token using refresh token.

        Args:
            refresh_token: Refresh token

        Returns:
            Tuple of (new_access_token, new_refresh_token) or (None, None) on failure
        """
        if not self.base_url:
            logger.error('Enterprise auth backend URL not configured')
            return None, None

        try:
            async with httpx.AsyncClient(timeout=ENTERPRISE_AUTH_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f'{self.base_url}/auth/refresh',
                    json={'refresh_token': refresh_token},
                    headers=self._get_headers(),
                )

                if response.status_code != 200:
                    logger.error(f'Enterprise auth token refresh failed: {response.status_code}')
                    return None, None

                data = response.json()
                access_token = data.get('access_token')
                new_refresh_token = data.get('refresh_token')

                if not access_token:
                    logger.error('No access_token in enterprise auth refresh response')
                    return None, None

                logger.debug('Successfully refreshed token from enterprise auth')
                return access_token, new_refresh_token

        except Exception as e:
            logger.exception(f'Exception when refreshing token: {e}')
            return None, None

    async def verify_token(self, access_token: str) -> bool:
        """Verify if a token is valid.

        First tries to decode locally, then optionally verifies with backend.

        Args:
            access_token: JWT access token

        Returns:
            True if valid, False otherwise
        """
        # Try local decode first
        payload = self.decode_jwt(access_token, verify=True)

        if not payload:
            return False

        # Optionally verify with backend
        if self.base_url:
            try:
                async with httpx.AsyncClient(timeout=ENTERPRISE_AUTH_HTTP_TIMEOUT) as client:
                    response = await client.get(
                        f'{self.base_url}/auth/verify',
                        headers=self._get_headers(access_token),
                    )
                    return response.status_code == 200
            except Exception:
                # Fall back to local verification
                pass

        return True

    def get_login_url(self, redirect_uri: str, state: str = '') -> str:
        """Get the URL for the enterprise login page.

        Args:
            redirect_uri: Where to redirect after login
            state: Optional state parameter for OAuth flow

        Returns:
            Login URL
        """
        url = f'{self.base_url}/auth/login?redirect_uri={redirect_uri}'
        if state:
            url += f'&state={state}'
        return url

    def get_auth_url(self, redirect_uri: str, state: str = '') -> str:
        """Get the URL for initiating OAuth flow.

        Args:
            redirect_uri: Where to redirect after authentication
            state: Optional state parameter

        Returns:
            OAuth authorization URL
        """
        url = f'{self.base_url}/auth/authorize?redirect_uri={redirect_uri}'
        if state:
            url += f'&state={state}'
        return url


# Singleton instances
_enterprise_auth_instances: dict[bool, EnterpriseAuthClient] = {}


def get_enterprise_auth_client(external: bool = False) -> EnterpriseAuthClient:
    """Get a singleton instance of EnterpriseAuthClient.

    Args:
        external: If True, use external URL for browser-facing operations

    Returns:
        EnterpriseAuthClient instance
    """
    if external not in _enterprise_auth_instances:
        _enterprise_auth_instances[external] = EnterpriseAuthClient(external=external)
    return _enterprise_auth_instances[external]


def is_enterprise_auth_enabled() -> bool:
    """Check if Enterprise authentication is enabled.

    Returns:
        True if Enterprise auth backend URL is configured, False otherwise
    """
    return bool(ENTERPRISE_AUTH_URL or ENTERPRISE_AUTH_URL_EXT)


# Backward compatibility aliases
# These allow existing code using old names to continue working
NestJSUserInfo = EnterpriseUserInfo
NestJSAuthClient = EnterpriseAuthClient
get_nestjs_auth_client = get_enterprise_auth_client
is_nestjs_auth_enabled = is_enterprise_auth_enabled
