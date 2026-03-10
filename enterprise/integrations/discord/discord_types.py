from abc import ABC, abstractmethod
from dataclasses import dataclass

from integrations.types import SummaryExtractionTracker
from jinja2 import Environment
from storage.discord_user import DiscordUser

from openhands.server.user_auth.user_auth import UserAuth


@dataclass
class DiscordMessageView:
    """Minimal view for sending messages to Discord.

    This class contains only the fields needed to send messages,
    without requiring user authentication. Can be used directly for
    simple message operations or as a base class for more complex views.
    """

    bot_token: str
    discord_user_id: str
    channel_id: int
    message_id: int
    thread_id: int | None
    guild_id: int

    def to_log_context(self) -> dict:
        """Return dict suitable for structured logging."""
        return {
            'discord_channel_id': self.channel_id,
            'discord_user_id': self.discord_user_id,
            'discord_guild_id': self.guild_id,
            'discord_thread_id': self.thread_id,
            'discord_message_id': self.message_id,
        }

    @classmethod
    async def from_payload(
        cls,
        payload: dict,
        discord_guild_store,
    ) -> 'DiscordMessageView | None':
        """Create a view from a raw Discord payload.

        This factory method handles the various payload formats from different
        Discord interactions (events, interactions, etc.).

        Args:
            payload: Raw Discord payload dictionary
            discord_guild_store: Store for retrieving bot tokens

        Returns:
            DiscordMessageView if all required fields are available,
            None if required fields are missing or bot token unavailable.
        """
        from openhands.core.logger import openhands_logger as logger

        # Discord uses 'guild_id' for server ID
        guild_id = payload.get('guild_id')

        # Channel ID can come from different places
        channel_id = (
            payload.get('channel_id')
            or payload.get('channel', {}).get('id')
        )

        # User ID
        user_id = (
            payload.get('author', {}).get('id')
            or payload.get('user', {}).get('id')
            or payload.get('discord_user_id')
        )

        # Message ID
        message_id = payload.get('id') or payload.get('message_id', 0)

        # Thread ID (if in a thread)
        thread_id = payload.get('thread_id') or payload.get('channel_id')

        if not guild_id or not channel_id or not user_id:
            logger.warning(
                'discord_message_view_from_payload_missing_fields',
                extra={
                    'has_guild_id': bool(guild_id),
                    'has_channel_id': bool(channel_id),
                    'has_user_id': bool(user_id),
                    'payload_keys': list(payload.keys()),
                },
            )
            return None

        bot_token = await discord_guild_store.get_guild_bot_token(str(guild_id))
        if not bot_token:
            logger.warning(
                'discord_message_view_from_payload_no_bot_token',
                extra={'guild_id': guild_id},
            )
            return None

        return cls(
            bot_token=bot_token,
            discord_user_id=str(user_id),
            channel_id=int(channel_id),
            message_id=int(message_id) if message_id else 0,
            thread_id=int(thread_id) if thread_id else None,
            guild_id=int(guild_id),
        )


class DiscordViewInterface(DiscordMessageView, SummaryExtractionTracker, ABC):
    """Interface for authenticated Discord views that can create conversations.

    All fields are required (non-None) because this interface is only used
    for users who have linked their Discord account to OpenHands.

    Inherits from DiscordMessageView:
        bot_token, discord_user_id, channel_id, message_id, thread_id, guild_id
    """

    user_msg: str
    discord_to_openhands_user: DiscordUser
    saas_user_auth: UserAuth
    selected_repo: str | None
    should_extract: bool
    send_summary_instruction: bool
    conversation_id: str
    v1_enabled: bool

    @abstractmethod
    async def _get_instructions(self, jinja_env: Environment) -> tuple[str, str]:
        """Instructions passed when conversation is first initialized."""
        pass

    @abstractmethod
    async def create_or_update_conversation(self, jinja_env: Environment):
        """Create a new conversation."""
        pass

    @abstractmethod
    def get_response_msg(self) -> str:
        pass


class StartingConvoException(Exception):
    """Raised when trying to send message to a conversation that is still starting up."""
