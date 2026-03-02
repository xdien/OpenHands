"""Base Messaging Integration Interface.

This module defines the abstract base class that all messaging integrations
must implement to work with OpenHands.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from openhands.messaging.config import MessagingProviderType
from openhands.messaging.stores.confirmation_store import ConfirmationStatus

if TYPE_CHECKING:
    from openhands.messaging.messaging_service import MessagingService


class BaseMessagingIntegration(ABC):
    """Abstract base class for messaging integrations.

    This class defines the interface that all messaging platform integrations
    (Telegram, Discord, Slack, etc.) must implement to work with OpenHands.

    The integration is responsible for:
    - Connecting to the external messaging platform API
    - Sending messages and confirmation requests to users
    - Receiving and processing incoming messages from users
    - Managing the lifecycle of the connection (start/stop)

    Attributes:
        allowed_user_ids: Set of external user IDs that are allowed to interact
        messaging_service: Reference to the parent MessagingService
    """

    def __init__(
        self,
        allowed_user_ids: set[str],
        messaging_service: 'MessagingService',
    ):
        """Initialize the messaging integration.

        Args:
            allowed_user_ids: Set of external user IDs (e.g., Telegram chat_ids)
                that are authorized to interact with OpenHands
            messaging_service: Reference to the parent MessagingService for
                accessing conversation stores and event callbacks
        """
        self.allowed_user_ids = allowed_user_ids
        self.messaging_service = messaging_service

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the integration name (e.g., 'telegram', 'discord').

        Returns:
            The name of the messaging platform
        """
        pass

    @property
    @abstractmethod
    def provider_type(self) -> MessagingProviderType:
        """Return the provider type enum.

        Returns:
            The MessagingProviderType enum value for this integration
        """
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the messaging service.

        This method should:
        - Initialize the connection to the messaging platform API
        - Start listening for incoming messages (polling or webhook)
        - Register command and callback handlers
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the messaging service gracefully.

        This method should:
        - Stop listening for new messages
        - Close any open connections
        - Clean up resources
        """
        pass

    @abstractmethod
    async def send_message(
        self, external_user_id: str, message: str, **kwargs: Any
    ) -> bool:
        """Send a message to an external user.

        Args:
            external_user_id: The user's ID on the messaging platform
                (e.g., Telegram chat_id)
            message: The message content to send
            **kwargs: Provider-specific options (parse_mode, reply_markup, etc.)

        Returns:
            True if message was sent successfully, False otherwise
        """
        pass

    @abstractmethod
    async def request_confirmation(
        self,
        external_user_id: str,
        action_type: str,
        action_details: str,
        action_content: str,
        conversation_id: str,
        timeout_seconds: int = 300,
    ) -> ConfirmationStatus:
        """Request user confirmation for an action.

        This method sends a confirmation request to the user and waits for
        their response. It should display the action details and provide
        options to approve or reject the action.

        Args:
            external_user_id: The user's ID on the messaging platform
            action_type: Type of action requiring confirmation (e.g., 'CmdRunAction')
            action_details: Human-readable description of the action
            action_content: Full action content for review
            conversation_id: OpenHands conversation ID
            timeout_seconds: How long to wait for confirmation before expiring

        Returns:
            ConfirmationStatus.CONFIRMED if user approved,
            ConfirmationStatus.REJECTED if user rejected,
            ConfirmationStatus.EXPIRED if timeout occurred
        """
        pass

    @abstractmethod
    async def handle_incoming_message(
        self, external_user_id: str, message_text: str, message_metadata: dict
    ) -> None:
        """Handle an incoming message from an external user.

        This method should:
        1. Validate the user is in the allowed_user_ids list
        2. Get or create the conversation mapping for this user
        3. Send the message to the OpenHands event stream
        4. Handle special commands (e.g., /start, /help, /status)

        Args:
            external_user_id: The user's ID on the messaging platform
            message_text: The text content of the message
            message_metadata: Additional metadata about the message
                (e.g., message_id, username, timestamp)
        """
        pass
