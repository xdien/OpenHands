"""Discord Manager for handling Discord integration with OpenHands.

This module provides:
- DiscordManager: Main class for processing Discord messages and managing conversations
- Authentication handling for Discord users
- Message sending and receiving
- Job lifecycle management
"""

from typing import Any

from integrations.manager import Manager
from integrations.models import Message, SourceType
from integrations.discord.discord_errors import DiscordError, DiscordErrorCode
from integrations.discord.discord_types import (
    DiscordMessageView,
    DiscordViewInterface,
    StartingConvoException,
)
from integrations.discord.discord_view import (
    DiscordFactory,
    DiscordNewConversationView,
    DiscordUpdateExistingConversationView,
)
from integrations.utils import (
    HOST_URL,
    OPENHANDS_RESOLVER_TEMPLATES_DIR,
    get_session_expired_message,
    infer_repo_from_message,
)
from integrations.v1_utils import get_saas_user_auth
from jinja2 import Environment, FileSystemLoader
from server.constants import DISCORD_BOT_TOKEN
from server.utils.conversation_callback_utils import register_callback_processor
from sqlalchemy import select
from storage.database import a_session_maker
from storage.discord_user import DiscordUser

from openhands.core.logger import openhands_logger as logger
from openhands.integrations.provider import ProviderHandler
from openhands.integrations.service_types import (
    AuthenticationError,
    ProviderTimeoutError,
    Repository,
)
from openhands.server.shared import config, server_config, sio
from openhands.server.types import (
    LLMAuthenticationError,
    MissingSettingsError,
    SessionExpiredError,
)
from openhands.server.user_auth.user_auth import UserAuth

# Key prefix for storing user messages in Redis during repo selection flow
DISCORD_USER_MSG_KEY_PREFIX = 'discord_user_msg'
# Expiration time for stored user messages (5 minutes)
DISCORD_USER_MSG_EXPIRATION = 300


class DiscordManager(Manager[DiscordViewInterface]):
    """Manager for Discord integration handling messages and conversations."""

    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.login_link = (
            'User has not yet authenticated: [Click here to Login to OpenHands]({}).'
        )

        self.jinja_env = Environment(
            loader=FileSystemLoader(OPENHANDS_RESOLVER_TEMPLATES_DIR + 'discord')
        )

    def _confirm_incoming_source_type(self, message: Message):
        if message.source != SourceType.DISCORD:
            raise ValueError(f'Unexpected message source {message.source}')

    async def authenticate_user(
        self, discord_user_id: str
    ) -> tuple[DiscordUser | None, UserAuth | None]:
        """Authenticate Discord user and get OpenHands user auth.

        Args:
            discord_user_id: The Discord user ID (snowflake)

        Returns:
            Tuple of (DiscordUser, UserAuth) if authenticated with Keycloak,
            (DiscordUser, None) if Discord user exists but not linked to Keycloak,
            (None, None) if Discord user not found
        """
        discord_user = None
        async with a_session_maker() as session:
            result = await session.execute(
                select(DiscordUser).where(
                    DiscordUser.discord_user_id == discord_user_id
                )
            )
            discord_user = result.scalar_one_or_none()

        saas_user_auth = None
        # Only get UserAuth if Discord user exists AND has keycloak_user_id
        # This allows Discord user to exist without Keycloak integration
        if discord_user and discord_user.keycloak_user_id:
            saas_user_auth = await get_saas_user_auth(
                discord_user.keycloak_user_id, self.token_manager
            )

        return discord_user, saas_user_auth

    async def _store_user_msg_for_form(
        self, message_id: int, thread_id: int | None, user_msg: str
    ) -> None:
        """Store user message in Redis for later retrieval when form is submitted.

        Args:
            message_id: The message ID (unique identifier)
            thread_id: The thread ID (if in a thread)
            user_msg: The original user message to store

        Raises:
            DiscordError: If storage fails (REDIS_STORE_FAILED)
        """
        key = f'{DISCORD_USER_MSG_KEY_PREFIX}:{message_id}:{thread_id}'
        try:
            redis = sio.manager.redis
            await redis.set(key, user_msg, ex=DISCORD_USER_MSG_EXPIRATION)
            logger.info(
                'discord_stored_user_msg',
                extra={
                    'message_id': message_id,
                    'thread_id': thread_id,
                    'key': key,
                },
            )
        except Exception as e:
            logger.error(
                'discord_store_user_msg_failed',
                extra={
                    'message_id': message_id,
                    'thread_id': thread_id,
                    'key': key,
                    'error': str(e),
                },
            )
            raise DiscordError(
                DiscordErrorCode.REDIS_STORE_FAILED,
                log_context={'message_id': message_id, 'thread_id': thread_id},
            )

    async def _retrieve_user_msg_for_form(
        self, message_id: int, thread_id: int | None
    ) -> str:
        """Retrieve stored user message from Redis.

        Args:
            message_id: The message ID
            thread_id: The thread ID (if in a thread)

        Returns:
            The stored user message

        Raises:
            DiscordError: If retrieval fails or message not found
        """
        key = f'{DISCORD_USER_MSG_KEY_PREFIX}:{message_id}:{thread_id}'
        try:
            redis = sio.manager.redis
            user_msg = await redis.get(key)
            if user_msg:
                if isinstance(user_msg, bytes):
                    user_msg = user_msg.decode('utf-8')
                logger.info(
                    'discord_retrieved_user_msg',
                    extra={
                        'message_id': message_id,
                        'thread_id': thread_id,
                        'key': key,
                    },
                )
                return user_msg
            else:
                logger.warning(
                    'discord_user_msg_not_found',
                    extra={
                        'message_id': message_id,
                        'thread_id': thread_id,
                        'key': key,
                    },
                )
                raise DiscordError(
                    DiscordErrorCode.SESSION_EXPIRED,
                    log_context={'message_id': message_id, 'thread_id': thread_id},
                )
        except DiscordError:
            raise
        except Exception as e:
            logger.error(
                'discord_retrieve_user_msg_failed',
                extra={
                    'message_id': message_id,
                    'thread_id': thread_id,
                    'key': key,
                    'error': str(e),
                },
            )
            raise DiscordError(
                DiscordErrorCode.REDIS_RETRIEVE_FAILED,
                log_context={'message_id': message_id, 'thread_id': thread_id},
            )

    async def _search_repositories(
        self, user_auth: UserAuth, query: str = '', per_page: int = 100
    ) -> list[Repository]:
        """Search repositories for a user with optional query filtering.

        Args:
            user_auth: The user's authentication context
            query: Search query to filter repositories
            per_page: Maximum number of results to return

        Returns:
            List of matching Repository objects
        """
        provider_tokens = await user_auth.get_provider_tokens()
        if provider_tokens is None:
            return []
        access_token = await user_auth.get_access_token()
        user_id = await user_auth.get_user_id()
        client = ProviderHandler(
            provider_tokens=provider_tokens,
            external_auth_token=access_token,
            external_auth_id=user_id,
        )
        repos: list[Repository] = await client.search_repositories(
            selected_provider=None,
            query=query,
            per_page=per_page,
            sort='pushed',
            order='desc',
            app_mode=server_config.app_mode,
        )
        return repos

    async def receive_message(self, message: Message):
        """Process an incoming Discord message.

        This is the single entry point for all Discord message processing.
        All DiscordErrors raised during processing are caught and handled here,
        sending appropriate error messages to the user.
        """
        self._confirm_incoming_source_type(message)

        try:
            discord_view = await self._process_message(message)
            if discord_view and await self.is_job_requested(message, discord_view):
                await self.start_job(discord_view)

        except DiscordError as e:
            await self.handle_discord_error(message.message, e)

        except Exception as e:
            logger.exception(
                'discord_unexpected_error',
                extra={'error': str(e), **message.message},
            )
            await self.handle_discord_error(
                message.message,
                DiscordError(DiscordErrorCode.UNEXPECTED_ERROR),
            )

    async def _process_message(self, message: Message) -> DiscordViewInterface | None:
        """Process message and return view if authenticated, or raise DiscordError.

        Returns:
            DiscordViewInterface if user is authenticated and ready to proceed,
            None if processing should stop (but no error).

        Raises:
            DiscordError: If user is not authenticated or other recoverable error.
        """
        discord_user, saas_user_auth = await self.authenticate_user(
            discord_user_id=message.message['discord_user_id']
        )

        discord_view = await DiscordFactory.create_discord_view_from_payload(
            message, discord_user, saas_user_auth
        )

        # Check if this is an unauthenticated user
        if not isinstance(discord_view, DiscordViewInterface):
            login_link = self._generate_login_link_with_state(message)
            raise DiscordError(
                DiscordErrorCode.USER_NOT_AUTHENTICATED,
                message_kwargs={'login_link': login_link},
                log_context=discord_view.to_log_context() if discord_view else {},
            )

        return discord_view

    def _generate_login_link_with_state(self, message: Message) -> str:
        """Generate OAuth login link with message state encoded."""
        import jwt
        jwt_secret = config.jwt_secret
        if not jwt_secret:
            raise ValueError('Must configure jwt_secret')
        state = jwt.encode(
            message.message, jwt_secret.get_secret_value(), algorithm='HS256'
        )
        # Return login URL with state
        return f'{HOST_URL}/discord/login?state={state}'

    async def handle_discord_error(self, payload: dict, error: DiscordError) -> None:
        """Handle a DiscordError by logging and sending user message.

        Args:
            payload: The Discord payload dict containing channel/user info
            error: The DiscordError to handle
        """
        # Log the error
        log_level = (
            'exception' if error.code == DiscordErrorCode.UNEXPECTED_ERROR else 'warning'
        )
        log_data = {
            'error_code': error.code.value,
            **error.log_context,
        }
        getattr(logger, log_level)(
            f'discord_error_{error.code.name.lower()}', extra=log_data
        )

        # Send user-facing message
        # For Discord, we need to send via the bot API
        await self._send_error_message(payload, error.get_user_message())

    async def _send_error_message(self, payload: dict, message: str) -> None:
        """Send an error message to Discord channel."""
        channel_id = payload.get('channel_id')
        thread_id = payload.get('thread_id')
        if channel_id:
            await self._post_to_discord(int(channel_id), message, thread_id)
        else:
            logger.warning(f'Discord error message (no channel_id): {message}')

    async def _post_to_discord(
        self,
        channel_id: int,
        content: str,
        thread_id: int | None = None,
    ) -> None:
        """Post a message to a Discord channel via REST API.

        Args:
            channel_id: The Discord channel ID to send to
            content: The text content to send
            thread_id: Optional thread ID if message should go to a thread
        """
        import httpx

        if not DISCORD_BOT_TOKEN:
            logger.warning('discord_send_no_bot_token')
            return

        # Truncate long messages (Discord limit is 2000 chars)
        if len(content) > 1990:
            content = content[:1990] + '…'

        target_channel = thread_id if thread_id else channel_id
        url = f'https://discord.com/api/v10/channels/{target_channel}/messages'
        headers = {
            'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
            'Content-Type': 'application/json',
        }
        payload_data = {'content': content}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload_data, headers=headers)
                if resp.status_code not in (200, 201):
                    logger.error(
                        'discord_send_message_failed',
                        extra={
                            'status': resp.status_code,
                            'channel_id': channel_id,
                            'thread_id': thread_id,
                            'response': resp.text[:200],
                        },
                    )
                else:
                    logger.info(
                        f'discord_message_sent to channel {target_channel}'
                    )
        except Exception as e:
            logger.error(f'discord_send_message_exception: {e}')

    async def send_message(
        self,
        message: str | dict[str, Any],
        discord_view: DiscordMessageView,
    ):
        """Send a message to Discord.

        Args:
            message: The message content. Can be a string (for simple text) or
                     a dict with 'text' and 'embed' keys (for structured messages).
            discord_view: The Discord view object containing channel info.
        """
        channel_id = discord_view.channel_id
        thread_id = discord_view.thread_id

        # Build text content
        if isinstance(message, dict):
            content = message.get('text', str(message))
        else:
            content = str(message) if message else ''

        if not content:
            logger.warning('discord_send_message_empty_content')
            return

        logger.info(
            f'discord_send_message to channel {channel_id}, thread {thread_id}: {content[:100]}'
        )
        await self._post_to_discord(channel_id, content, thread_id)

    async def _try_verify_inferred_repo(
        self, discord_view: DiscordNewConversationView
    ) -> bool:
        """Try to infer and verify a repository from the user's message.

        Returns:
            True if a valid repo was found and verified, False otherwise
        """
        user = discord_view.discord_to_openhands_user
        inferred_repos = infer_repo_from_message(discord_view.user_msg)

        if len(inferred_repos) != 1:
            return False

        inferred_repo = inferred_repos[0]
        logger.info(
            f'[Discord] Verifying inferred repo "{inferred_repo}" '
            f'for user {user.discord_username} (id={discord_view.saas_user_auth.get_user_id()})'
        )

        try:
            provider_tokens = await discord_view.saas_user_auth.get_provider_tokens()
            if not provider_tokens:
                return False

            access_token = await discord_view.saas_user_auth.get_access_token()
            user_id = await discord_view.saas_user_auth.get_user_id()
            provider_handler = ProviderHandler(
                provider_tokens=provider_tokens,
                external_auth_token=access_token,
                external_auth_id=user_id,
            )
            repo = await provider_handler.verify_repo_provider(inferred_repo)
            discord_view.selected_repo = repo.full_name
            return True
        except (AuthenticationError, ProviderTimeoutError) as e:
            logger.info(
                f'[Discord] Could not verify repo "{inferred_repo}": {e}. '
                f'Showing repository selector.'
            )
            return False

    async def is_job_requested(
        self, message: Message, discord_view: DiscordViewInterface
    ) -> bool:
        """Determine if a job should be started based on the current context.

        Args:
            message: The incoming message
            discord_view: The Discord view

        Returns:
            True if job should start, False if waiting for user input
        """
        # Check if view type allows immediate start
        if isinstance(discord_view, DiscordUpdateExistingConversationView):
            return True
        if isinstance(discord_view, DiscordNewConversationView):
            if discord_view.selected_repo:
                return True
            # Try to infer repo
            if await self._try_verify_inferred_repo(discord_view):
                return True
            # Show repo selection (simplified for now)
            logger.info(f'[Discord] No repo selected, user needs to specify one')

        return False

    async def start_job(self, discord_view: DiscordViewInterface) -> None:
        """Start an OpenHands job from Discord.

        Args:
            discord_view: The Discord view with job context
        """
        try:
            msg_info = None
            user_info = discord_view.discord_to_openhands_user
            try:
                logger.info(
                    f'[Discord] Starting job for user {user_info.discord_username} (id={user_info.discord_user_id})',
                    extra={'keyloak_user_id': user_info.keycloak_user_id},
                )
                conversation_id = await discord_view.create_or_update_conversation(
                    self.jinja_env
                )

                logger.info(
                    f'[Discord] Created conversation {conversation_id} for user {user_info.discord_username}'
                )

                msg_info = discord_view.get_response_msg()

            except MissingSettingsError as e:
                logger.warning(
                    f'[Discord] Missing settings error for user {user_info.discord_username}: {str(e)}'
                )
                msg_info = f'{user_info.discord_username} please re-login into [OpenHands Cloud]({HOST_URL}) before starting a job.'

            except LLMAuthenticationError as e:
                logger.warning(
                    f'[Discord] LLM authentication error for user {user_info.discord_username}: {str(e)}'
                )
                msg_info = f'@{user_info.discord_username} please set a valid LLM API key in [OpenHands Cloud]({HOST_URL}) before starting a job.'

            except SessionExpiredError as e:
                logger.warning(
                    f'[Discord] Session expired for user {user_info.discord_username}: {str(e)}'
                )
                msg_info = get_session_expired_message(user_info.discord_username)

            except StartingConvoException as e:
                msg_info = str(e)

            await self.send_message(msg_info, discord_view)

        except Exception:
            logger.exception('[Discord]: Error starting job')
            await self.send_message(
                'Uh oh! There was an unexpected error starting the job :(', discord_view
            )
