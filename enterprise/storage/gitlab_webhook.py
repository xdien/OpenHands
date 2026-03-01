import sys
from enum import IntEnum
from typing import List

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.types import TypeDecorator, Text
from storage.base import Base


class ArrayAsText(TypeDecorator):
    """Custom type that stores arrays as text in SQLite and as ARRAY in PostgreSQL."""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_ARRAY(Text))
        return dialect.type_descriptor(Text)

    def process_result_value(self, value, dialect):
        if dialect.name == 'postgresql':
            return value
        # For SQLite, stored as text, need to parse
        if value is None:
            return None
        # Value is stored as JSON string
        import json
        return json.loads(value)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == 'postgresql':
            return value
        # For SQLite, store as JSON string
        import json
        return json.dumps(value)


class WebhookStatus(IntEnum):
    PENDING = 0  # Conditions for installation webhook need checking
    VERIFIED = 1  # Conditions are met for installing webhook
    RATE_LIMITED = 2  # API was rate limited, failed to check
    INVALID = 3  # Unexpected error occur when checking (keycloak connection, etc)


class GitlabWebhook(Base):  # type: ignore
    """
    Represents a Gitlab webhook configuration for a repository or group.
    """

    __tablename__ = 'gitlab_webhook'
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String, nullable=True)
    project_id = Column(String, nullable=True)
    user_id = Column(String, nullable=False)
    webhook_exists = Column(Boolean, nullable=False)
    webhook_url = Column(String, nullable=True)
    webhook_secret = Column(String, nullable=True)
    webhook_uuid = Column(String, nullable=True)
    # Use ArrayAsText for cross-database compatibility (SQLite stores as JSON, PostgreSQL as ARRAY)
    scopes = Column(ArrayAsText, nullable=True)
    last_synced = Column(
        DateTime,
        server_default=text('CURRENT_TIMESTAMP'),
        onupdate=text('CURRENT_TIMESTAMP'),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f'<GitlabWebhook(id={self.id}, group_id={self.group_id}, '
            f'project_id={self.project_id}, last_synced={self.last_synced})>'
        )
