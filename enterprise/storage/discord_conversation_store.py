"""Discord conversation store for managing Discord conversation mappings."""

import logging
from storage.database import a_session_maker
from storage.discord_conversation import DiscordConversation
from sqlalchemy import select, text

logger = logging.getLogger(__name__)


class DiscordConversationStore:
    """Store for Discord conversation mappings."""

    async def get_discord_conversation(
        self, channel_id: str, thread_id: str | None = None
    ) -> DiscordConversation | None:
        """Get a discord conversation by channel_id and thread_id.

        Args:
            channel_id: The Discord channel ID
            thread_id: The Discord thread ID (optional)

        Returns:
            DiscordConversation if found, None otherwise
        """
        try:
            async with a_session_maker() as session:
                # First try to find by thread_id if provided
                if thread_id:
                    stmt = select(DiscordConversation).where(
                        DiscordConversation.discord_thread_id == thread_id
                    )
                    result = await session.execute(stmt)
                    existing = result.scalars().first()
                    if existing:
                        return existing

                # Fall back to channel_id (get most recent)
                stmt = select(DiscordConversation).where(
                    DiscordConversation.discord_channel_id == channel_id
                ).order_by(DiscordConversation.created_at.desc())
                result = await session.execute(stmt)
                return result.scalars().first()
        except Exception as e:
            logger.warning(f'Failed to get discord conversation: {e}')
            return None

    async def create_discord_conversation(
        self, discord_conversation: DiscordConversation
    ) -> None:
        """Create a new discord conversation mapping.

        Args:
            discord_conversation: The DiscordConversation to create
        """
        try:
            async with a_session_maker() as session:
                await session.merge(discord_conversation)
                await session.commit()
        except Exception as e:
            logger.warning(f'Failed to create discord conversation: {e}')

    @classmethod
    def get_instance(cls) -> 'DiscordConversationStore':
        return cls()
