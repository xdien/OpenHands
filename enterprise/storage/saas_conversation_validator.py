from server.auth.auth_error import AuthError, ExpiredError
from server.auth.saas_user_auth import saas_user_auth_from_signed_token
from server.auth.token_manager import TokenManager
from socketio.exceptions import ConnectionRefusedError
from storage.api_key_store import ApiKeyStore

from openhands.core.config import load_openhands_config
from openhands.core.logger import openhands_logger as logger
from openhands.server.shared import ConversationStoreImpl
from openhands.storage.conversation.conversation_validator import ConversationValidator


class SaasConversationValidator(ConversationValidator):
    """Storage for conversation metadata. May or may not support multiple users depending on the environment."""

    async def _validate_api_key(self, api_key: str) -> str | None:
        """
        Validate an API key and return the user_id if valid.

        Args:
            api_key: The API key to validate

        Returns:
            The user_id if the API key is valid, None otherwise
        """
        try:
            token_manager = TokenManager()

            # Validate the API key and get the user_id
            api_key_store = ApiKeyStore.get_instance()
            logger.info(f'Validating API key, key prefix: {api_key[:10]}...')
            validation_result = await api_key_store.validate_api_key(api_key)

            if not validation_result:
                logger.warning('Invalid API key - validation_result is None')
                return None

            user_id = validation_result.user_id
            logger.info(f'API key validated, user_id: {user_id}')

            if not user_id:
                logger.warning('API key validated but user_id is None')
                return None

            # Get the offline token for the user
            offline_token = await token_manager.load_offline_token(user_id)
            if not offline_token:
                logger.warning(f'No offline token found for user {user_id}')
                return None

            return user_id

        except Exception as e:
            logger.warning(f'Error validating API key: {str(e)}', exc_info=True)
            return None

    async def _validate_conversation_access(
        self, conversation_id: str, user_id: str
    ) -> bool:
        """
        Validate that the user has access to the conversation.

        Args:
            conversation_id: The ID of the conversation
            user_id: The ID of the user
            github_user_id: The GitHub user ID, if available

        Returns:
            True if the user has access to the conversation, False otherwise

        Raises:
            ConnectionRefusedError: If the user does not have access to the conversation
        """
        config = load_openhands_config()
        conversation_store = await ConversationStoreImpl.get_instance(config, user_id)

        if not await conversation_store.validate_metadata(conversation_id, user_id):
            logger.error(
                f'User {user_id} is not allowed to join conversation {conversation_id}'
            )
            raise ConnectionRefusedError(
                f'User {user_id} is not allowed to join conversation {conversation_id}'
            )
        return True

    async def validate(
        self,
        conversation_id: str,
        cookies_str: str,
        authorization_header: str | None = None,
        session_api_key: str | None = None,
    ) -> str | None:
        """
        Validate the conversation access using either an API key from the Authorization header,
        session_api_key from query params, or a keycloak_auth cookie.

        Args:
            conversation_id: The ID of the conversation
            cookies_str: The cookies string from the request
            authorization_header: The Authorization header from the request, if available
            session_api_key: The session API key from query params, if available

        Returns:
            A tuple of (user_id, github_user_id)

        Raises:
            ConnectionRefusedError: If the user does not have access to the conversation
            AuthError: If the authentication fails
            RuntimeError: If there is an error with the configuration or user info
        """
        logger.info(
            'SaasConversationValidator.validate() called',
            extra={
                'session_id': conversation_id,
                'session_api_key': repr(session_api_key),
                'has_cookies': bool(cookies_str),
                'has_auth_header': bool(authorization_header),
            },
        )

        # Import needed for all auth methods
        from openhands.core.config import load_openhands_config

        # Try to authenticate using session_api_key from query params first
        # session_api_key is a session key generated from jwt_secret + conversation_id
        # It's deterministic, so we can validate by regenerating it
        # Note: frontend might send "null" string instead of actual null
        if (
            session_api_key
            and session_api_key != 'null'
            and session_api_key != 'undefined'
        ):
            logger.info(
                f'Attempting session_api_key validation for conversation {conversation_id}',
                extra={
                    'session_id': conversation_id,
                    'has_session_api_key': bool(session_api_key),
                },
            )

            # Validate session_api_key by regenerating it and comparing
            import hashlib
            from base64 import urlsafe_b64encode

            from openhands.server.config.server_config import ServerConfig

            config = load_openhands_config()
            server_config = ServerConfig()
            jwt_secret = server_config.jwt_secret.get_secret_value()

            # Regenerate expected session_api_key
            conversation_key = f'{jwt_secret}:{conversation_id}'.encode()
            expected_session_api_key = urlsafe_b64encode(
                hashlib.sha256(conversation_key).digest()
            ).decode()

            if session_api_key == expected_session_api_key:
                # session_api_key is valid - now get user_id from conversation metadata
                from openhands.server.shared import ConversationStoreImpl

                conversation_store = await ConversationStoreImpl.get_instance(
                    config, None
                )
                try:
                    metadata = await conversation_store.get_metadata(conversation_id)
                    if metadata and metadata.user_id:
                        logger.info(
                            f'User {metadata.user_id} is connecting to conversation {conversation_id} via session_api_key'
                        )
                        await self._validate_conversation_access(
                            conversation_id, metadata.user_id
                        )
                        return metadata.user_id
                    else:
                        logger.warning(
                            'session_api_key valid but no user_id found in conversation metadata',
                            extra={'session_id': conversation_id},
                        )
                except Exception as e:
                    logger.warning(
                        f'Error getting conversation metadata: {e}',
                        extra={'session_id': conversation_id},
                    )
            else:
                logger.warning(
                    'session_api_key validation failed - key mismatch',
                    extra={
                        'session_id': conversation_id,
                        'expected_prefix': expected_session_api_key[:10],
                    },
                )
        else:
            logger.info(
                f'No session_api_key provided for conversation {conversation_id}, falling back to other auth methods',
                extra={'session_id': conversation_id},
            )

        # Try to authenticate using Authorization header
        if authorization_header and authorization_header.startswith('Bearer '):
            api_key = authorization_header.replace('Bearer ', '')
            user_id = await self._validate_api_key(api_key)

            if user_id:
                logger.info(
                    f'User {user_id} is connecting to conversation {conversation_id} via API key'
                )

                await self._validate_conversation_access(conversation_id, user_id)
                return user_id
            else:
                logger.warning(
                    f'API key validation failed for conversation {conversation_id} - falling back to cookie auth',
                    extra={'session_id': conversation_id},
                )

        # Fall back to cookie authentication
        token_manager = TokenManager()
        config = load_openhands_config()
        cookies = (
            dict(cookie.split('=', 1) for cookie in cookies_str.split('; '))
            if cookies_str
            else {}
        )

        signed_token = cookies.get('keycloak_auth', '')
        if not signed_token:
            logger.warning(
                'No keycloak_auth cookie or valid Authorization header',
                extra={'session_id': conversation_id},
            )
            raise ConnectionRefusedError(
                'No keycloak_auth cookie or valid Authorization header'
            )
        if not config.jwt_secret:
            raise RuntimeError('JWT secret not found')

        try:
            logger.info(
                'Attempting to validate keycloak_auth token',
                extra={'session_id': conversation_id},
            )
            user_auth = await saas_user_auth_from_signed_token(signed_token)
            logger.info('Got user_auth object, getting access token')
            access_token = await user_auth.get_access_token()
            logger.info(
                'Got access token',
                extra={'session_id': conversation_id, 'has_token': bool(access_token)},
            )
        except ExpiredError:
            logger.warning('Token expired', extra={'session_id': conversation_id})
            raise ConnectionRefusedError('SESSION$TIMEOUT_MESSAGE')
        except Exception as e:
            logger.warning(
                f'Error validating token: {type(e).__name__}',
                extra={'session_id': conversation_id},
                exc_info=True,
            )
            raise

        if access_token is None:
            logger.warning('No access token', extra={'session_id': conversation_id})
            raise AuthError('no_access_token')

        logger.info('Getting user info from token manager')
        user_info = await token_manager.get_user_info(access_token.get_secret_value())
        # sub is a required field in KeycloakUserInfo, validation happens in get_user_info
        user_id = user_info.sub
        logger.info(
            'Got user_id from token',
            extra={'session_id': conversation_id},
        )

        logger.info(f'User {user_id} is connecting to conversation {conversation_id}')

        await self._validate_conversation_access(conversation_id, user_id)  # type: ignore
        return user_id
