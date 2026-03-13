"""Conversation Store for Messaging Interface.

This module provides storage for mappings between external messaging users
and OpenHands conversations.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import Field

from openhands.sdk.utils.models import OpenHandsModel


class UserConversationMapping(OpenHandsModel):
    """Maps an external messaging user to an OpenHands conversation.

    This mapping allows the messaging interface to route messages between
    external users (e.g., Telegram chat IDs) and their corresponding
    OpenHands conversations.

    Attributes:
        external_user_id: The user's ID on the messaging platform (e.g., Telegram chat_id)
        conversation_id: The OpenHands conversation ID
        created_at: When this mapping was created
        updated_at: When this mapping was last updated
        is_active: Whether this mapping is currently active
    """

    external_user_id: str = Field(
        ..., description='External messaging user ID (e.g., Telegram chat_id)'
    )
    conversation_id: str = Field(..., description='OpenHands conversation ID')
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description='When this mapping was created'
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description='When this mapping was last updated',
    )
    is_active: bool = Field(
        default=True, description='Whether this mapping is currently active'
    )

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()


class ConversationStore(ABC):
    """Abstract base class for conversation storage.

    This store manages mappings between external messaging users and
    OpenHands conversations. Implementations can use in-memory storage,
    databases, or other persistence mechanisms.
    """

    @abstractmethod
    async def get_mapping_by_external_user_id(
        self, external_user_id: str
    ) -> UserConversationMapping | None:
        """Get the conversation mapping for an external user ID.

        Args:
            external_user_id: The external user ID to look up

        Returns:
            UserConversationMapping if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_mapping_by_conversation_id(
        self, conversation_id: str
    ) -> UserConversationMapping | None:
        """Get the conversation mapping for a conversation ID.

        Args:
            conversation_id: The OpenHands conversation ID to look up

        Returns:
            UserConversationMapping if found, None otherwise
        """
        pass

    @abstractmethod
    async def create_mapping(
        self, external_user_id: str, conversation_id: str
    ) -> UserConversationMapping:
        """Create a new user-conversation mapping.

        Args:
            external_user_id: The external user ID
            conversation_id: The OpenHands conversation ID

        Returns:
            The created UserConversationMapping
        """
        pass

    @abstractmethod
    async def update_mapping(
        self, mapping: UserConversationMapping
    ) -> UserConversationMapping:
        """Update an existing user-conversation mapping.

        Args:
            mapping: The mapping to update

        Returns:
            The updated UserConversationMapping
        """
        pass

    @abstractmethod
    async def deactivate_mapping(
        self, external_user_id: str | None = None, conversation_id: str | None = None
    ) -> bool:
        """Deactivate a mapping by external user ID or conversation ID.

        Args:
            external_user_id: The external user ID to deactivate, or None
            conversation_id: The conversation ID to deactivate, or None

        Returns:
            True if a mapping was deactivated, False if not found
        """
        pass


class InMemoryConversationStore(ConversationStore):
    """In-memory implementation of ConversationStore.

    This store keeps mappings in memory only. It's suitable for development
    and testing, but not for production use where persistence is required.
    """

    def __init__(self):
        """Initialize the in-memory conversation store."""
        # Maps external_user_id -> UserConversationMapping
        self._by_external_user_id: dict[str, UserConversationMapping] = {}
        # Maps conversation_id -> UserConversationMapping
        self._by_conversation_id: dict[str, UserConversationMapping] = {}

    async def get_mapping_by_external_user_id(
        self, external_user_id: str
    ) -> UserConversationMapping | None:
        """Get the conversation mapping for an external user ID."""
        mapping = self._by_external_user_id.get(external_user_id)
        if mapping and mapping.is_active:
            return mapping
        return None

    async def get_mapping_by_conversation_id(
        self, conversation_id: str
    ) -> UserConversationMapping | None:
        """Get the conversation mapping for a conversation ID."""
        mapping = self._by_conversation_id.get(conversation_id)
        if mapping and mapping.is_active:
            return mapping
        return None

    async def create_mapping(
        self, external_user_id: str, conversation_id: str
    ) -> UserConversationMapping:
        """Create a new user-conversation mapping."""
        # Deactivate any existing mappings for these IDs
        await self.deactivate_mapping(external_user_id=external_user_id)
        await self.deactivate_mapping(conversation_id=conversation_id)

        mapping = UserConversationMapping(
            external_user_id=external_user_id,
            conversation_id=conversation_id,
        )

        self._by_external_user_id[external_user_id] = mapping
        self._by_conversation_id[conversation_id] = mapping

        return mapping

    async def update_mapping(
        self, mapping: UserConversationMapping
    ) -> UserConversationMapping:
        """Update an existing user-conversation mapping."""
        mapping.touch()

        self._by_external_user_id[mapping.external_user_id] = mapping
        self._by_conversation_id[mapping.conversation_id] = mapping

        return mapping

    async def deactivate_mapping(
        self, external_user_id: str | None = None, conversation_id: str | None = None
    ) -> bool:
        """Deactivate a mapping by external user ID or conversation ID."""
        mapping: UserConversationMapping | None = None

        if external_user_id:
            mapping = self._by_external_user_id.get(external_user_id)

        if not mapping and conversation_id:
            mapping = self._by_conversation_id.get(conversation_id)

        if not mapping or not mapping.is_active:
            return False

        mapping.is_active = False
        mapping.touch()

        # Remove from active indexes
        self._by_external_user_id.pop(mapping.external_user_id, None)
        self._by_conversation_id.pop(mapping.conversation_id, None)

        return True
