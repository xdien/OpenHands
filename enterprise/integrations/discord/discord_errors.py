"""Centralized error handling for Discord integration.

This module provides:
- DiscordErrorCode: Unique error codes for traceability
- DiscordError: Exception class for user-facing errors
- get_user_message(): Function to get user-facing messages for error codes
"""

import logging
from enum import Enum
from typing import Any

from integrations.utils import HOST_URL

logger = logging.getLogger(__name__)


class DiscordErrorCode(Enum):
    """Unique error codes for traceability in logs and user messages."""

    SESSION_EXPIRED = 'DISCORD_ERR_001'
    REDIS_STORE_FAILED = 'DISCORD_ERR_002'
    REDIS_RETRIEVE_FAILED = 'DISCORD_ERR_003'
    USER_NOT_AUTHENTICATED = 'DISCORD_ERR_004'
    PROVIDER_TIMEOUT = 'DISCORD_ERR_005'
    PROVIDER_AUTH_FAILED = 'DISCORD_ERR_006'
    LLM_AUTH_FAILED = 'DISCORD_ERR_007'
    MISSING_SETTINGS = 'DISCORD_ERR_008'
    DISCORD_LINKED_NO_OPENHANDS = 'DISCORD_ERR_009'
    REPO_NOT_FOUND = 'DISCORD_ERR_010'
    UNEXPECTED_ERROR = 'DISCORD_ERR_999'


class DiscordError(Exception):
    """Exception for errors that should be communicated to the Discord user.

    This exception is caught by the centralized error handler in DiscordManager,
    which logs the error and sends an appropriate message to the user.

    Usage:
        raise DiscordError(DiscordErrorCode.USER_NOT_AUTHENTICATED,
                          message_kwargs={'login_link': link})
    """

    def __init__(
        self,
        code: DiscordErrorCode,
        message_kwargs: dict[str, Any] | None = None,
        log_context: dict[str, Any] | None = None,
    ):
        """Initialize a DiscordError.

        Args:
            code: The error code identifying the type of error
            message_kwargs: Kwargs for formatting the user message
                           (e.g., {'login_link': '...'})
            log_context: Additional context for structured logging
        """
        self.code = code
        self.message_kwargs = message_kwargs or {}
        self.log_context = log_context or {}
        super().__init__(f'{code.value}: {code.name}')

    def get_user_message(self) -> str:
        """Get the user-facing message for this error."""
        return get_user_message(self.code, **self.message_kwargs)


# Centralized user-facing messages
_USER_MESSAGES: dict[DiscordErrorCode, str] = {
    DiscordErrorCode.SESSION_EXPIRED: (
        '⏰ Your session has expired. '
        'Please mention me again with your request to start a new conversation.'
    ),
    DiscordErrorCode.REDIS_STORE_FAILED: (
        '⚠️ Something went wrong on our end (ref: {code}). '
        'Please try again in a few moments.'
    ),
    DiscordErrorCode.REDIS_RETRIEVE_FAILED: (
        '⚠️ Something went wrong on our end (ref: {code}). '
        'Please try again in a few moments.'
    ),
    DiscordErrorCode.USER_NOT_AUTHENTICATED: (
        '🔐 Please link your Discord account to OpenHands first: '
        '[Click here to Link Account]({login_link})\n\n'
        'ℹ️ **Note:** This will allow the bot to access your OpenHands settings and API keys. '
        'If Keycloak is not configured, your Discord account will still be registered for future use.'
    ),
    DiscordErrorCode.PROVIDER_TIMEOUT: (
        '⏱️ The request timed out while connecting to your git provider. '
        'Please try again.'
    ),
    DiscordErrorCode.PROVIDER_AUTH_FAILED: (
        '🔐 Authentication with your git provider failed. '
        f'Please re-login at [OpenHands Cloud]({HOST_URL}) and try again.'
    ),
    DiscordErrorCode.LLM_AUTH_FAILED: (
        '@{username} please set a valid LLM API key in '
        f'[OpenHands Cloud]({HOST_URL}) before starting a job.'
    ),
    DiscordErrorCode.MISSING_SETTINGS: (
        '{username} please re-login into '
        f'[OpenHands Cloud]({HOST_URL}) before starting a job.'
    ),
    DiscordErrorCode.UNEXPECTED_ERROR: (
        'Uh oh! There was an unexpected error (ref: {code}). Please try again later.'
    ),
    DiscordErrorCode.DISCORD_LINKED_NO_OPENHANDS: (
        '✅ Your Discord account **@{discord_username}** is already linked!\n\n'
        '⚠️ However, your OpenHands account is not connected yet.\n'
        'To use the bot\'s full features, please click here to complete setup: '
        '[🔗 Link OpenHands Account]({login_link})\n\n'
        'Once linked, mention me again and I\'ll be ready to help! 🚀'
    ),
    DiscordErrorCode.REPO_NOT_FOUND: (
        '🔍 I couldn\'t figure out which repository to work on.\n\n'
        'Please specify the repository in your message, for example:\n'
        '`@OpenHands fix the styling in user:repo`'
    ),
}


def get_user_message(error_code: DiscordErrorCode, **kwargs) -> str:
    """Get a user-facing message for a given error code.

    Args:
        error_code: The error code to get a message for
        **kwargs: Additional formatting arguments (e.g., username, login_link)

    Returns:
        Formatted user-facing message string
    """
    msg = _USER_MESSAGES.get(
        error_code, _USER_MESSAGES[DiscordErrorCode.UNEXPECTED_ERROR]
    )
    try:
        return msg.format(code=error_code.value, **kwargs)
    except KeyError as e:
        logger.warning(
            f'Missing format key {e} in error message',
            extra={'error_code': error_code.value},
        )
        # Return a generic error message with the code for debugging
        return f'An error occurred (ref: {error_code.value}). Please try again later.'
