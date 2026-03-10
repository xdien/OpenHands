from sqlalchemy import Column, DateTime, ForeignKey, Identity, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from storage.base import Base


class DiscordUser(Base):  # type: ignore
    """Discord user storage model for linking Discord users to OpenHands users."""

    __tablename__ = 'discord_users'
    id = Column(Integer, Identity(), primary_key=True)
    keycloak_user_id = Column(String, nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey('org.id'), nullable=True)
    discord_user_id = Column(String, nullable=False, index=True)
    discord_username = Column(String, nullable=False)
    discord_discriminator = Column(String, nullable=True)  # Legacy discriminator (e.g., #1234)
    created_at = Column(
        DateTime,
        server_default=text('CURRENT_TIMESTAMP'),
        nullable=False,
    )

    # Relationships
    org = relationship('Org', back_populates='discord_users')
