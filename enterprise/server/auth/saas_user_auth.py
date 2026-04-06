import time
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

import jwt
from fastapi import Request
from keycloak.exceptions import KeycloakError
from pydantic import SecretStr
from server.auth.auth_error import (
    AuthError,
    BearerTokenError,
    CookieError,
    ExpiredError,
    NoCredentialsError,
)
from server.auth.authorization import (
    get_role_permissions,
    get_user_org_role,
)
from server.auth.constants import BITBUCKET_DATA_CENTER_HOST
from server.auth.token_manager import TokenManager
from server.config import get_config
from server.logger import logger
from server.rate_limit import RateLimiter, create_redis_rate_limiter
from sqlalchemy import delete, select
from storage.api_key_store import ApiKeyStore
from storage.auth_tokens import AuthTokens
from storage.database import a_session_maker
from storage.org_store import OrgStore
from storage.saas_secrets_store import SaasSecretsStore
from storage.saas_settings_store import SaasSettingsStore
from storage.user_authorization import UserAuthorizationType
from storage.user_authorization_store import UserAuthorizationStore
from storage.user_store import UserStore
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from openhands.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderToken,
    ProviderType,
)
from openhands.server.settings import Settings
from openhands.server.user_auth.user_auth import AuthType, UserAuth
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.settings.settings_store import SettingsStore

token_manager = TokenManager()


rate_limiter: RateLimiter = create_redis_rate_limiter('10/second; 100/minute')


@dataclass
class SaasUserAuth(UserAuth):
    refresh_token: SecretStr
    user_id: str
    email: str | None = None
    email_verified: bool | None = None
    access_token: SecretStr | None = None
    provider_tokens: PROVIDER_TOKEN_TYPE | None = None
    refreshed: bool = False
    settings_store: SaasSettingsStore | None = None
    secrets_store: SaasSecretsStore | None = None
    _settings: Settings | None = None
    _secrets: Secrets | None = None
    accepted_tos: bool | None = None
    auth_type: AuthType = AuthType.COOKIE
    # API key context fields - populated when authenticated via API key
    api_key_org_id: UUID | None = None  # Org bound to the API key used for auth
    api_key_id: int | None = None
    api_key_name: str | None = None
    # Organization context fields - populated lazily via get_org_info()
    _org_id: str | None = None
    _org_name: str | None = None
    _role: str | None = None
    _permissions: list[str] | None = None
    _org_info_loaded: bool = False

    def get_api_key_org_id(self) -> UUID | None:
        """Get the organization ID bound to the API key used for authentication.

        Returns:
            The org_id if authenticated via API key with org binding, None otherwise
            (cookie auth or legacy API keys without org binding).
        """
        return self.api_key_org_id

    async def get_user_id(self) -> str | None:
        return self.user_id

    async def get_user_email(self) -> str | None:
        return self.email

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(KeycloakError),
    )
    async def refresh(self):
        # Safely check refresh_token without triggering SecretStr truthiness issues
        refresh_token_value = self.refresh_token.get_secret_value() if isinstance(self.refresh_token, SecretStr) and self.refresh_token.get_secret_value() else None
        logger.debug(
            'saas_user_auth_refresh_start',
            extra={
                'user_id': self.user_id,
                'has_refresh_token': self.refresh_token is not None,
                'refresh_token_type': type(self.refresh_token).__name__ if self.refresh_token is not None else None,
                'refresh_token_prefix': refresh_token_value[:50] if refresh_token_value else None,
            },
        )
        if self.refresh_token is None:
            logger.warning('saas_user_auth_refresh_no_token', extra={'user_id': self.user_id})
            raise ExpiredError()
        # Check if refresh_token is a SecretStr
        if not hasattr(self.refresh_token, 'get_secret_value'):
            logger.error('saas_user_auth_refresh_invalid_token_type', extra={'user_id': self.user_id, 'type': type(self.refresh_token).__name__})
            raise ExpiredError()
        if self._is_token_expired(self.refresh_token):
            logger.debug('saas_user_auth_refresh:expired')
            raise ExpiredError()

        # Check if enterprise auth is enabled
        from server.auth.constants import ENTERPRISE_AUTH_URL, ENTERPRISE_AUTH_URL_EXT
        logger.debug(
            'saas_user_auth_refresh_check_auth',
            extra={
                'ENTERPRISE_AUTH_URL': ENTERPRISE_AUTH_URL,
                'ENTERPRISE_AUTH_URL_EXT': ENTERPRISE_AUTH_URL_EXT,
            },
        )

        tokens = await token_manager.refresh(self.refresh_token.get_secret_value())
        self.access_token = SecretStr(tokens['access_token'])
        self.refresh_token = SecretStr(tokens['refresh_token'])
        self.refreshed = True
        if not self.email or not self.email_verified or not self.user_id:
            # We don't need to verify the signature here because we just refreshed
            # this token from the IDP via token_manager.refresh()
            access_token_payload = jwt.decode(
                tokens['access_token'], options={'verify_signature': False}
            )
            # Support both standard 'sub' claim and enterprise 'userId' claim
            self.user_id = access_token_payload.get('sub') or access_token_payload.get('userId')
            self.email = access_token_payload.get('email', '')
            self.email_verified = access_token_payload.get('email_verified', False)

    def _is_token_expired(self, token: SecretStr | None):
        logger.debug('saas_user_auth_is_token_expired')
        # Handle None token
        if token is None:
            logger.warning('saas_user_auth_is_token_expired_token_is_none')
            return True
        # Handle non-SecretStr token
        if not hasattr(token, 'get_secret_value'):
            logger.warning('saas_user_auth_is_token_expired_invalid_token_type', extra={'type': type(token).__name__})
            return True
        # Decode token payload - works with both access and refresh tokens
        try:
            payload = jwt.decode(
                token.get_secret_value(), options={'verify_signature': False}
            )
        except Exception as e:
            logger.error('saas_user_auth_is_token_expired_decode_error', extra={'error': str(e)})
            return True

        # Sanity check - make sure we refer to current user
        # Support both 'sub' (Keycloak) and 'userId' (enterprise auth)
        token_user_id = payload.get('sub') or payload.get('userId')
        if token_user_id and token_user_id != self.user_id:
            logger.warning('saas_user_auth_token_user_mismatch: expected=%s, got=%s', self.user_id, token_user_id)
        # Don't assert - just log warning, as the token might still be valid

        # Check token expiration
        expiration = payload.get('exp')
        if expiration:
            logger.debug('saas_user_auth_is_token_expired expiration is %d', expiration)
        return expiration and expiration < time.time()

        # Sanity check - make sure we refer to current user
        # Support both 'sub' (Keycloak) and 'userId' (enterprise auth)
        token_user_id = payload.get('sub') or payload.get('userId')
        if token_user_id and token_user_id != self.user_id:
            logger.warning('saas_user_auth_token_user_mismatch: expected=%s, got=%s', self.user_id, token_user_id)
        # Don't assert - just log warning, as the token might still be valid

        # Check token expiration
        expiration = payload.get('exp')
        if expiration:
            logger.debug('saas_user_auth_is_token_expired expiration is %d', expiration)
        return expiration and expiration < time.time()

    def get_auth_type(self) -> AuthType | None:
        return self.auth_type

    async def get_user_settings(self) -> Settings | None:
        settings = self._settings
        if settings:
            return settings
        settings_store = await self.get_user_settings_store()
        settings = await settings_store.load()
        if settings:
            settings.email = self.email
            settings.email_verified = self.email_verified
            self._settings = settings
        return settings

    async def get_secrets_store(self) -> SaasSecretsStore:
        logger.debug('saas_user_auth_get_secrets_store')
        secrets_store = self.secrets_store
        if secrets_store:
            return secrets_store
        secrets_store = SaasSecretsStore(self.user_id, get_config())
        self.secrets_store = secrets_store
        return secrets_store

    async def get_secrets(self):
        user_secrets = self._secrets
        if user_secrets:
            return user_secrets
        secrets_store = await self.get_secrets_store()
        user_secrets = await secrets_store.load()
        self._secrets = user_secrets
        return user_secrets

    async def get_access_token(self) -> SecretStr | None:
        logger.debug('saas_user_auth_get_access_token')
        try:
            # Check if access_token is None or has no value
            if self.access_token is None or not self.access_token.get_secret_value():
                # Check if we have a refresh token before trying to refresh
                # Use isinstance check to avoid SecretStr truthiness issues
                if self.refresh_token is None or not hasattr(self.refresh_token, 'get_secret_value'):
                    logger.warning('saas_user_auth_get_access_token_no_refresh_token', extra={'user_id': self.user_id})
                    return None
                await self.refresh()
            elif self._is_token_expired(self.access_token):
                # Access token expired, try to refresh
                if self.refresh_token is None or not hasattr(self.refresh_token, 'get_secret_value'):
                    logger.warning('saas_user_auth_get_access_token_no_refresh_token', extra={'user_id': self.user_id})
                    return None
                await self.refresh()
            return self.access_token
        except AuthError:
            raise
        except ExpiredError:
            logger.warning('saas_user_auth_get_access_token_token_expired', extra={'user_id': self.user_id})
            return None
        except Exception as e:
            import traceback
            # Safely extract token info without triggering SecretStr truthiness issues
            logger.warning('saas_user_auth_get_access_token_error', extra={
                'user_id': self.user_id,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'access_token_type': type(self.access_token).__name__ if self.access_token is not None else None,
                'refresh_token_type': type(self.refresh_token).__name__ if self.refresh_token is not None else None,
            })
            return None

    async def get_provider_tokens(self) -> PROVIDER_TOKEN_TYPE | None:
        logger.debug('saas_user_auth_get_provider_tokens')
        if self.provider_tokens is not None:
            return self.provider_tokens
        provider_tokens = {}
        access_token = await self.get_access_token()
        if not access_token:
            logger.warning('saas_user_auth_get_provider_tokens_no_access_token', extra={'user_id': self.user_id})
            return {}

        user_secrets = await self.get_secrets()

        try:
            # TODO: I think we can do this in a single request if we refactor
            async with a_session_maker() as session:
                result = await session.execute(
                    select(AuthTokens).where(
                        AuthTokens.keycloak_user_id == self.user_id
                    )
                )
                tokens = result.scalars().all()
                logger.info(
                    'saas_user_auth_get_provider_tokens_query',
                    extra={
                        'user_id': self.user_id,
                        'tokens_found': len(tokens),
                        'token_providers': [t.identity_provider for t in tokens] if tokens else [],
                    }
                )

            # Debug: Log user_secrets status
            logger.info(
                'saas_user_auth_get_provider_tokens_debug',
                extra={
                    'user_id': self.user_id,
                    'user_secrets_exists': user_secrets is not None,
                    'user_secrets_provider_tokens': user_secrets.provider_tokens if user_secrets else None,
                }
            )

            # Fallback: Check user_secrets.provider_tokens if auth_tokens table is empty
            # This handles tokens saved via frontend UI (Settings → GitHub token)
            if not tokens and user_secrets and user_secrets.provider_tokens:
                logger.info(
                    'saas_user_auth_get_provider_tokens_fallback',
                    extra={
                        'user_id': self.user_id,
                        'fallback_providers': list(user_secrets.provider_tokens.keys()),
                    }
                )
                for idp_type, provider_token in user_secrets.provider_tokens.items():
                    if provider_token.token:
                        provider_tokens[idp_type] = ProviderToken(
                            token=provider_token.token,
                            user_id=provider_token.user_id,
                            host=provider_token.host,
                        )
                if provider_tokens:
                    self.provider_tokens = MappingProxyType(provider_tokens)
                    return self.provider_tokens

            for token in tokens:
                idp_type = ProviderType(token.identity_provider)
                try:
                    host = None
                    if user_secrets and idp_type in user_secrets.provider_tokens:
                        host = user_secrets.provider_tokens[idp_type].host

                    if idp_type == ProviderType.BITBUCKET_DATA_CENTER and not host:
                        host = BITBUCKET_DATA_CENTER_HOST or None

                    provider_token = await token_manager.get_idp_token(
                        access_token.get_secret_value(),
                        idp=idp_type,
                    )
                    # TODO: Currently we don't store the IDP user id in our refresh table. We should.
                    provider_tokens[idp_type] = ProviderToken(
                        token=SecretStr(provider_token), user_id=None, host=host
                    )
                except Exception as e:
                    # If there was a problem with a refresh token we log and delete it
                    logger.error(
                        f'Error refreshing provider_token token: {e}',
                        extra={
                            'user_id': self.user_id,
                            'idp_type': token.identity_provider,
                        },
                    )
                    async with a_session_maker() as session:
                        await session.execute(
                            delete(AuthTokens).where(AuthTokens.id == token.id)
                        )
                        await session.commit()
                    raise

            self.provider_tokens = MappingProxyType(provider_tokens)
            return self.provider_tokens
        except Exception as e:
            # Any error refreshing tokens means we need to log in again
            raise AuthError() from e

    async def get_user_settings_store(self) -> SettingsStore:
        settings_store = self.settings_store
        if settings_store:
            return settings_store
        settings_store = SaasSettingsStore(self.user_id, get_config())
        self.settings_store = settings_store
        return settings_store

    async def get_mcp_api_key(self) -> str:
        api_key_store = ApiKeyStore.get_instance()
        mcp_api_key = await api_key_store.retrieve_mcp_api_key(self.user_id)
        if not mcp_api_key:
            mcp_api_key = await api_key_store.create_api_key(
                self.user_id, 'MCP_API_KEY', None
            )
        return mcp_api_key

    async def get_org_info(self) -> dict | None:
        """Get organization info for the current user.

        Lazily loads and caches organization data including:
        - org_id: Current organization ID
        - org_name: Current organization name
        - role: User's role in the organization
        - permissions: List of permission names for the role

        Returns:
            dict with org_id, org_name, role, permissions or None if not available
        """
        if self._org_info_loaded:
            if self._org_id is None:
                return None
            return {
                'org_id': self._org_id,
                'org_name': self._org_name,
                'role': self._role,
                'permissions': self._permissions,
            }

        # Mark as loaded to avoid repeated attempts on failure
        self._org_info_loaded = True

        try:
            # Get user and their current org
            user = await UserStore.get_user_by_id(self.user_id)
            if not user:
                logger.warning(f'User {self.user_id} not found for org info')
                return None

            # Get the current org
            org = await OrgStore.get_org_by_id(user.current_org_id)
            if not org:
                logger.warning(
                    f'Organization {user.current_org_id} not found for user {self.user_id}'
                )
                return None

            # Get user's role in the current org
            role = await get_user_org_role(self.user_id, user.current_org_id)
            role_name = role.name if role else None

            # Get permissions for the role
            permissions: list[str] = []
            if role_name:
                role_permissions = get_role_permissions(role_name)
                permissions = [p.value for p in role_permissions]

            # Cache the results
            self._org_id = str(user.current_org_id)
            self._org_name = org.name
            self._role = role_name
            self._permissions = permissions

            return {
                'org_id': self._org_id,
                'org_name': self._org_name,
                'role': self._role,
                'permissions': self._permissions,
            }
        except Exception as e:
            logger.error(f'Error fetching org info for user {self.user_id}: {e}')
            return None

    @classmethod
    async def get_instance(cls, request: Request) -> UserAuth:
        logger.debug('saas_user_auth_get_instance')
        # First we check for for an API Key...
        logger.debug('saas_user_auth_get_instance:check_bearer')
        instance = await saas_user_auth_from_bearer(request)
        if instance is None:
            logger.debug('saas_user_auth_get_instance:check_cookie')
            instance = await saas_user_auth_from_cookie(request)
        if instance is None:
            logger.debug('saas_user_auth_get_instance:no_credentials')
            raise NoCredentialsError('failed to authenticate')
        if not getattr(request.state, 'user_rate_limit_processed', False):
            user_id = await instance.get_user_id()
            if user_id:
                # Ensure requests are only counted once
                request.state.user_rate_limit_processed = True
                # Will raise if rate limit is reached.
                await rate_limiter.hit('auth_uid', user_id)
        return instance

    @classmethod
    async def get_for_user(cls, user_id: str) -> UserAuth:
        offline_token = await token_manager.load_offline_token(user_id)
        assert offline_token is not None
        return SaasUserAuth(
            user_id=user_id,
            refresh_token=SecretStr(offline_token),
            auth_type=AuthType.BEARER,
        )


def get_api_key_from_header(request: Request):
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        return auth_header.replace('Bearer ', '')

    # This is a temp hack
    # Streamable HTTP MCP Client works via redirect requests, but drops the Authorization header for reason
    # We include `X-Session-API-Key` header by default due to nested runtimes, so it used as a drop in replacement here
    session_api_key = request.headers.get('X-Session-API-Key')
    if session_api_key:
        return session_api_key

    # Fallback to X-Access-Token header as an additional option
    return request.headers.get('X-Access-Token')


async def saas_user_auth_from_bearer(request: Request) -> SaasUserAuth | None:
    try:
        api_key = get_api_key_from_header(request)
        if not api_key:
            return None

        api_key_store = ApiKeyStore.get_instance()
        validation_result = await api_key_store.validate_api_key(api_key)
        if not validation_result:
            return None
        offline_token = await token_manager.load_offline_token(
            validation_result.user_id
        )
        saas_user_auth = SaasUserAuth(
            user_id=validation_result.user_id,
            refresh_token=SecretStr(offline_token),
            auth_type=AuthType.BEARER,
            api_key_org_id=validation_result.org_id,
            api_key_id=validation_result.key_id,
            api_key_name=validation_result.key_name,
        )
        await saas_user_auth.refresh()
        return saas_user_auth
    except Exception as exc:
        raise BearerTokenError from exc


async def saas_user_auth_from_cookie(request: Request) -> SaasUserAuth | None:
    try:
        signed_token = request.cookies.get('keycloak_auth')
        if not signed_token:
            return None
        return await saas_user_auth_from_signed_token(signed_token)
    except Exception as exc:
        raise CookieError from exc


async def saas_user_auth_from_signed_token(signed_token: str) -> SaasUserAuth:
    logger.debug('saas_user_auth_from_signed_token')
    jwt_secret = get_config().jwt_secret.get_secret_value()
    decoded = jwt.decode(signed_token, jwt_secret, algorithms=['HS256'])
    logger.debug('saas_user_auth_from_signed_token:decoded')
    access_token = decoded['access_token']
    refresh_token = decoded['refresh_token']
    logger.debug('saas_user_auth_from_signed_token')
    accepted_tos = decoded.get('accepted_tos')

    # The access token was encoded using HS256. Since we signed it, we can trust it was
    # created by us. So we can grab the user_id and expiration from it without going back to keycloak.
    # Support both Keycloak (sub, email) and Enterprise (userId) token formats
    access_token_payload = jwt.decode(access_token, options={'verify_signature': False})

    # Support multiple user ID claim names
    user_id = (
        access_token_payload.get('sub') or
        access_token_payload.get('userId') or
        access_token_payload.get('user_id') or
        access_token_payload.get('id')
    )

    if not user_id:
        logger.error(
            'saas_user_auth_from_signed_token_no_user_id',
            extra={'token_payload_keys': list(access_token_payload.keys())},
        )
        return None

    # Email may not be present in enterprise tokens
    email = access_token_payload.get('email')
    # For enterprise tokens without email_verified field, default to True
    # since enterprise auth already validates users through their own system
    # Only Keycloak tokens have email_verified field
    email_verified = access_token_payload.get('email_verified', True)

    # Check if email is blacklisted (whitelist takes precedence)
    if email:
        auth_type = await UserAuthorizationStore.get_authorization_type(email, None)
        if auth_type == UserAuthorizationType.BLACKLIST:
            logger.warning(
                f'Blocked authentication attempt for existing user with email: {email}'
            )
            raise AuthError(
                'Access denied: Your email domain is not allowed to access this service'
            )

    logger.debug('saas_user_auth_from_signed_token:return')

    return SaasUserAuth(
        access_token=SecretStr(access_token),
        refresh_token=SecretStr(refresh_token),
        user_id=user_id,
        email=email,
        email_verified=email_verified,
        accepted_tos=accepted_tos,
        auth_type=AuthType.COOKIE,
    )


async def get_user_auth_from_keycloak_id(keycloak_user_id: str) -> UserAuth:
    offline_token = await token_manager.load_offline_token(keycloak_user_id)
    if offline_token is None:
        logger.info('no_offline_token_found')

    user_auth = SaasUserAuth(
        user_id=keycloak_user_id,
        refresh_token=SecretStr(offline_token),
    )
    return user_auth
