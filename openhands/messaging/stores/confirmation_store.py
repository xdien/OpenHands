"""Confirmation Store for Messaging Interface.

This module provides storage for pending action confirmations requested
from external messaging users.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum

from pydantic import Field

from openhands.sdk.utils.models import OpenHandsModel


class ConfirmationStatus(str, Enum):
    """Status of a confirmation request."""

    PENDING = 'pending'
    """Waiting for user response"""

    CONFIRMED = 'confirmed'
    """User has confirmed the action"""

    REJECTED = 'rejected'
    """User has rejected the action"""

    EXPIRED = 'expired'
    """Confirmation request has timed out"""


class PendingConfirmation(OpenHandsModel):
    """Represents a pending action confirmation request.

    When an action requires user confirmation, a PendingConfirmation
    object is created and stored until the user responds or the
    request expires.

    Attributes:
        id: Unique confirmation ID (UUID string)
        conversation_id: OpenHands conversation ID
        external_user_id: External messaging user ID (e.g., Telegram chat_id)
        action_type: Type of action requiring confirmation (e.g., 'CmdRunAction')
        action_details: Human-readable action description
        action_content: Full action content for review
        status: Current confirmation status
        created_at: When this confirmation was created
        expires_at: When this confirmation expires
        callback_message_id: ID of the message sent to the user (for editing)
    """

    id: str = Field(..., description='Unique confirmation ID')
    conversation_id: str = Field(..., description='OpenHands conversation ID')
    external_user_id: str = Field(..., description='External messaging user ID')
    action_type: str = Field(..., description='Type of action (CmdRunAction, etc.)')
    action_details: str = Field(..., description='Human-readable action description')
    action_content: str = Field(..., description='Full action content for review')
    status: ConfirmationStatus = Field(
        default=ConfirmationStatus.PENDING, description='Current confirmation status'
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description='When this confirmation was created',
    )
    expires_at: datetime | None = Field(
        default=None, description='Confirmation expiration time'
    )
    callback_message_id: int | None = Field(
        default=None, description='Telegram message ID for the confirmation request'
    )

    @property
    def is_expired(self) -> bool:
        """Check if this confirmation has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_pending(self) -> bool:
        """Check if this confirmation is still pending."""
        return self.status == ConfirmationStatus.PENDING and not self.is_expired

    def set_expiration(self, timeout_seconds: int) -> None:
        """Set the expiration time based on a timeout in seconds.

        Args:
            timeout_seconds: Number of seconds until expiration
        """
        self.expires_at = datetime.utcnow() + timedelta(seconds=timeout_seconds)


class ConfirmationStore(ABC):
    """Abstract base class for confirmation storage.

    This store manages pending confirmation requests from external users.
    Implementations can use in-memory storage, databases, or other
    persistence mechanisms.
    """

    @abstractmethod
    async def get_confirmation(
        self, confirmation_id: str
    ) -> PendingConfirmation | None:
        """Get a pending confirmation by ID.

        Args:
            confirmation_id: The confirmation ID to look up

        Returns:
            PendingConfirmation if found, None otherwise
        """
        pass

    @abstractmethod
    async def create_confirmation(
        self, confirmation: PendingConfirmation
    ) -> PendingConfirmation:
        """Create a new pending confirmation.

        Args:
            confirmation: The PendingConfirmation to store

        Returns:
            The stored PendingConfirmation
        """
        pass

    @abstractmethod
    async def update_confirmation(
        self, confirmation: PendingConfirmation
    ) -> PendingConfirmation:
        """Update an existing confirmation.

        Args:
            confirmation: The PendingConfirmation to update

        Returns:
            The updated PendingConfirmation
        """
        pass

    @abstractmethod
    async def delete_confirmation(self, confirmation_id: str) -> bool:
        """Delete a confirmation by ID.

        Args:
            confirmation_id: The ID of the confirmation to delete

        Returns:
            True if deleted, False if not found
        """
        pass

    @abstractmethod
    async def get_pending_confirmations_for_user(
        self, external_user_id: str
    ) -> list[PendingConfirmation]:
        """Get all pending confirmations for a user.

        Args:
            external_user_id: The external user ID to query

        Returns:
            List of pending PendingConfirmation objects
        """
        pass

    @abstractmethod
    async def get_pending_confirmations_for_conversation(
        self, conversation_id: str
    ) -> list[PendingConfirmation]:
        """Get all pending confirmations for a conversation.

        Args:
            conversation_id: The OpenHands conversation ID to query

        Returns:
            List of pending PendingConfirmation objects
        """
        pass

    @abstractmethod
    async def cleanup_expired_confirmations(self) -> int:
        """Remove all expired confirmations from storage.

        Returns:
            Number of confirmations removed
        """
        pass


class InMemoryConfirmationStore(ConfirmationStore):
    """In-memory implementation of ConfirmationStore.

    This store keeps confirmations in memory only. It's suitable for
    development and testing, but not for production use where
    persistence is required.
    """

    def __init__(self):
        """Initialize the in-memory confirmation store."""
        # Maps confirmation_id -> PendingConfirmation
        self._confirmations: dict[str, PendingConfirmation] = {}

    async def get_confirmation(
        self, confirmation_id: str
    ) -> PendingConfirmation | None:
        """Get a pending confirmation by ID."""
        return self._confirmations.get(confirmation_id)

    async def create_confirmation(
        self, confirmation: PendingConfirmation
    ) -> PendingConfirmation:
        """Create a new pending confirmation."""
        self._confirmations[confirmation.id] = confirmation
        return confirmation

    async def update_confirmation(
        self, confirmation: PendingConfirmation
    ) -> PendingConfirmation:
        """Update an existing confirmation."""
        self._confirmations[confirmation.id] = confirmation
        return confirmation

    async def delete_confirmation(self, confirmation_id: str) -> bool:
        """Delete a confirmation by ID."""
        if confirmation_id in self._confirmations:
            del self._confirmations[confirmation_id]
            return True
        return False

    async def get_pending_confirmations_for_user(
        self, external_user_id: str
    ) -> list[PendingConfirmation]:
        """Get all pending confirmations for a user."""
        return [
            c
            for c in self._confirmations.values()
            if c.external_user_id == external_user_id and c.is_pending
        ]

    async def get_pending_confirmations_for_conversation(
        self, conversation_id: str
    ) -> list[PendingConfirmation]:
        """Get all pending confirmations for a conversation."""
        return [
            c
            for c in self._confirmations.values()
            if c.conversation_id == conversation_id and c.is_pending
        ]

    async def cleanup_expired_confirmations(self) -> int:
        """Remove all expired confirmations from storage."""
        expired_ids = [cid for cid, c in self._confirmations.items() if c.is_expired]

        for cid in expired_ids:
            del self._confirmations[cid]

        return len(expired_ids)
