"""Messaging Service for OpenHands.

This module provides the main messaging service that orchestrates
messaging integrations and manages conversation mappings.
"""

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

from openhands.messaging.base import BaseMessagingIntegration
from openhands.messaging.config import MessagingConfig
from openhands.messaging.stores.confirmation_store import (
    ConfirmationStatus,
    ConfirmationStore,
    InMemoryConfirmationStore,
)
from openhands.messaging.stores.conversation_store import (
    ConversationStore,
    InMemoryConversationStore,
    UserConversationMapping,
)

if TYPE_CHECKING:
    from fastapi import Request

    from openhands.app_server.services.injector import InjectorState


class ConversationNotFoundError(Exception):
    """Raised when a conversation mapping is not found for a user."""

    def __init__(self, message: str, external_user_id: str | None = None):
        super().__init__(message)
        self.external_user_id = external_user_id


logger = logging.getLogger(__name__)


class MessagingService:
    """Main messaging service for OpenHands.

    This service orchestrates messaging integrations, manages conversation
    mappings, and handles communication between external messaging platforms
    and the OpenHands event system.

    Responsibilities:
    - Initialize and manage messaging integrations (Telegram, etc.)
    - Map external users to OpenHands conversations
    - Handle incoming messages from external users
    - Request confirmations and process responses
    - Send task results and notifications

    Attributes:
        config: Messaging configuration
        conversation_store: Store for user-conversation mappings
        confirmation_store: Store for pending confirmations
        integration: Active messaging integration
    """

    def __init__(
        self,
        config: MessagingConfig,
        conversation_store: ConversationStore | None = None,
        confirmation_store: ConfirmationStore | None = None,
    ):
        """Initialize the messaging service.

        Args:
            config: Messaging configuration
            conversation_store: Optional custom conversation store.
                If not provided, uses InMemoryConversationStore.
            confirmation_store: Optional custom confirmation store.
                If not provided, uses InMemoryConfirmationStore.
        """
        self.config = config
        self.conversation_store = conversation_store or InMemoryConversationStore()
        self.confirmation_store = confirmation_store or InMemoryConfirmationStore()
        self.integration: BaseMessagingIntegration | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the messaging service and start the integration.

        This method creates the appropriate messaging integration based
        on the configuration and starts it.
        """
        if not self.config.enabled:
            logger.info('Messaging service is disabled in configuration')
            return

        if self._initialized:
            logger.warning('Messaging service already initialized')
            return

        # Create integration based on provider type
        allowed_user_ids = set(self.config.allowed_user_ids)

        # Create integration based on provider type
        # Note: Future providers will be added here. Currently only TELEGRAM is supported.
        allowed_user_ids = set(self.config.allowed_user_ids)

        # Initialize Telegram integration
        from openhands.messaging.telegram import TelegramIntegration

        telegram_config = self.config.get_telegram_config()
        self.integration = TelegramIntegration(
            config=telegram_config,
            allowed_user_ids=allowed_user_ids,
            messaging_service=self,
        )
        await self.integration.start()
        logger.info('Telegram integration started successfully')
        self._initialized = True

        try:
            from openhands.messaging.telegram import TelegramIntegration

            telegram_config = self.config.get_telegram_config()
            self.integration = TelegramIntegration(
                config=telegram_config,
                allowed_user_ids=allowed_user_ids,
                messaging_service=self,
            )
            await self.integration.start()
            logger.info('Telegram integration started successfully')
            self._initialized = True
        except ImportError as e:
            logger.error(f'Failed to import Telegram integration: {e}')
            raise
        except Exception as e:
            logger.error(f'Failed to start Telegram integration: {e}')
            raise

    async def shutdown(self) -> None:
        """Shutdown the messaging service and stop the integration."""
        if not self._initialized:
            return

        if self.integration:
            await self.integration.stop()
            logger.info(f'{self.integration.name} integration stopped')

        self._initialized = False

    async def handle_incoming_message(
        self, external_user_id: str, message_text: str, message_metadata: dict
    ) -> None:
        """Handle an incoming message from an external user.

        This method:
        1. Validates the user is authorized
        2. Gets or creates the conversation mapping
        3. Sends the message to the OpenHands event stream

        Args:
            external_user_id: The external user ID (e.g., Telegram chat_id)
            message_text: The message content
            message_metadata: Additional metadata about the message
        """
        # Check if user is authorized
        if external_user_id not in self.config.allowed_user_ids:
            logger.warning(
                f'Unauthorized user attempted to send message: {external_user_id}'
            )
            return

        # Get or create conversation mapping
        mapping = await self.conversation_store.get_mapping_by_external_user_id(
            external_user_id
        )

        if not mapping:
            # No conversation mapping exists - raise exception to indicate
            # the integration layer needs to create a new conversation
            logger.info(f'No active conversation for user {external_user_id}')
            raise ConversationNotFoundError(
                f'No conversation mapping found for user {external_user_id}',
                external_user_id=external_user_id,
            )

        # Send message to event stream - this is handled by the
        # conversation manager which is injected externally
        logger.info(
            f'Received message from {external_user_id} for conversation '
            f'{mapping.conversation_id}: {message_text[:50]}...'
        )

    async def get_user_for_conversation(self, conversation_id: str) -> str | None:
        """Get the external user ID for a conversation.

        Args:
            conversation_id: The OpenHands conversation ID

        Returns:
            External user ID if found, None otherwise
        """
        mapping = await self.conversation_store.get_mapping_by_conversation_id(
            conversation_id
        )
        if mapping:
            return mapping.external_user_id
        return None

    async def get_conversation_status(self, external_user_id: str) -> str:
        """Get the current conversation status for a user.

        Args:
            external_user_id: The external user ID

        Returns:
            Human-readable status message
        """
        mapping = await self.conversation_store.get_mapping_by_external_user_id(
            external_user_id
        )

        if not mapping:
            return 'No active conversation. Send a message to start a new task.'

        # Check for pending confirmations
        pending = await self.confirmation_store.get_pending_confirmations_for_user(
            external_user_id
        )

        if pending:
            return (
                f'Active conversation: {mapping.conversation_id}\n'
                f'Pending confirmations: {len(pending)}\n'
                f'Latest: {pending[0].action_type}'
            )

        return f'Active conversation: {mapping.conversation_id}\nStatus: Running'

    async def cancel_conversation(self, external_user_id: str) -> None:
        """Cancel the active conversation for a user.

        Args:
            external_user_id: The external user ID
        """
        await self.conversation_store.deactivate_mapping(
            external_user_id=external_user_id
        )
        logger.info(f'Cancelled conversation for user {external_user_id}')

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

        Args:
            external_user_id: The external user ID
            action_type: Type of action requiring confirmation
            action_details: Human-readable action description
            action_content: Full action content for review
            conversation_id: OpenHands conversation ID
            timeout_seconds: How long to wait for confirmation

        Returns:
            ConfirmationStatus indicating user's response
        """
        if not self.integration:
            logger.error('Messaging integration not initialized')
            return ConfirmationStatus.EXPIRED

        return await self.integration.request_confirmation(
            external_user_id=external_user_id,
            action_type=action_type,
            action_details=action_details,
            action_content=action_content,
            conversation_id=conversation_id,
            timeout_seconds=timeout_seconds,
        )

    async def send_task_result(
        self, external_user_id: str, conversation_id: str, state: str, reason: str = ''
    ) -> None:
        """Send task completion result to a user.

        Args:
            external_user_id: The external user ID
            conversation_id: The OpenHands conversation ID
            state: Final agent state (FINISHED, ERROR, REJECTED)
            reason: Optional reason or error message
        """
        if not self.integration:
            logger.error('Messaging integration not initialized')
            return

        # Format result message
        if state == 'FINISHED':
            message = '✅ Task completed successfully!'
        elif state == 'ERROR':
            message = f'❌ Task encountered an error:\n{reason}'
        elif state == 'REJECTED':
            message = f'⏹️ Task was rejected:\n{reason}'
        else:
            message = f'Task finished with state: {state}'

        await self.integration.send_message(
            external_user_id=external_user_id,
            message=message,
        )

    async def create_conversation_mapping(
        self, external_user_id: str, conversation_id: str
    ) -> UserConversationMapping:
        """Create a new user-conversation mapping.

        Args:
            external_user_id: The external user ID
            conversation_id: The OpenHands conversation ID

        Returns:
            The created UserConversationMapping
        """
        return await self.conversation_store.create_mapping(
            external_user_id=external_user_id,
            conversation_id=conversation_id,
        )


class MessagingServiceInjector:
    """Dependency injector for MessagingService.

    This injector creates and configures MessagingService instances
    for dependency injection in the FastAPI application.

    This injector follows the OpenHands injector pattern, providing
    context() and depends() methods for FastAPI integration.
    """

    def __init__(self, config: MessagingConfig | None = None):
        """Initialize the messaging service injector.

        Args:
            config: Optional messaging configuration. If not provided,
                the service will be disabled.
        """
        self.config = config
        self._service: MessagingService | None = None

    async def _get_or_create_service(self) -> MessagingService:
        """Get or create the messaging service instance.

        Returns:
            Configured MessagingService instance
        """
        if self._service is None:
            if not self.config:
                # Return a disabled service
                self._service = MessagingService(config=MessagingConfig(enabled=False))
            else:
                self._service = MessagingService(config=self.config)
                # Initialize the service (starts the integration)
                await self._service.initialize()

        return self._service

    async def inject(
        self, state: 'InjectorState', request: 'Request | None' = None
    ) -> AsyncGenerator[MessagingService, None]:
        """Create and yield a MessagingService instance.

        This method follows the OpenHands injector pattern, yielding
        the service instance for dependency injection.

        Args:
            state: The injector state (from FastAPI app.state)
            request: Optional FastAPI request object

        Yields:
            Configured MessagingService instance
        """
        _ = state  # Unused but required by interface
        _ = request  # Unused but required by interface
        service = await self._get_or_create_service()
        yield service

    @asynccontextmanager
    async def context(
        self, state: 'InjectorState', request: 'Request | None' = None
    ) -> AsyncGenerator[MessagingService, None]:
        """Context manager for the messaging service.

        Args:
            state: The injector state
            request: Optional FastAPI request object

        Yields:
            MessagingService instance
        """
        async for service in self.inject(state, request):
            yield service

    async def depends(
        self, request: 'Request'
    ) -> AsyncGenerator[MessagingService, None]:
        """FastAPI dependency injection entry point.

        Args:
            request: The FastAPI request object

        Yields:
            Configured MessagingService instance
        """
        async for service in self.inject(request.state, request):
            yield service

    async def shutdown(self) -> None:
        """Shutdown the messaging service and clean up resources.

        This method should be called during application shutdown.
        """
        if self._service is not None:
            await self._service.shutdown()
            self._service = None
