"""Discord conversation storage model for mapping Discord threads to conversations."""

from sqlalchemy import Column, DateTime, Integer, String, text
from storage.base import Base


class DiscordConversation(Base):  # type: ignore
    """Discord conversation mapping for linking Discord threads to OpenHands conversations."""

    __tablename__ = 'discord_conversations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        String,
        nullable=False,
        index=True,
    )
    keycloak_user_id = Column(
        String,
        nullable=True,
        index=True,
    )
    discord_channel_id = Column(
        String,
        nullable=False,
        index=True,
    )
    discord_thread_id = Column(
        String,
        nullable=True,
        index=True,
    )
    discord_message_id = Column(
        String,
        nullable=True,
    )
    discord_guild_id = Column(
        String,
        nullable=True,
    )
    created_at = Column(
        DateTime,
        server_default=text('CURRENT_TIMESTAMP'),
        nullable=False,
    )
