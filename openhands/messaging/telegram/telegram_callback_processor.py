"""Telegram Callback Processor for Event Callback System.

This module provides the EventCallbackProcessor implementation for Telegram,
which processes OpenHands events and sends notifications via Telegram.
"""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from openhands.app_server.event_callback.event_callback_models import (
    EventCallback,
    EventCallbackProcessor,
    EventCallbackResult,
    EventCallbackResultStatus,
)
from openhands.sdk import Event

if TYPE_CHECKING:
    from openhands.messaging.messaging_service import MessagingService

logger = logging.getLogger(__name__)


class TelegramCallbackProcessor(EventCallbackProcessor):
    """Processes events and sends notifications via Telegram.

    This callback processor is registered with the OpenHands event callback
    system and processes events to send Telegram notifications for:
    - Action confirmation requests
    - Task completion events
    - Agent state changes

    Attributes:
        messaging_service: Reference to the MessagingService
    """

    def __init__(self, messaging_service: 'MessagingService'):
        """Initialize the Telegram callback processor.

        Args:
            messaging_service: Reference to the MessagingService for
                sending notifications and managing confirmations
        """
        self.messaging_service = messaging_service

    async def __call__(
        self,
        conversation_id: UUID,
        callback: EventCallback,
        event: Event,
    ) -> EventCallbackResult | None:
        """Process an event and send Telegram notification if needed.

        This method is called by the event callback system when an event
        matching the callback's filter is triggered.

        Args:
            conversation_id: The conversation UUID
            callback: The EventCallback that triggered this processing
            event: The Event object to process

        Returns:
            EventCallbackResult indicating success or failure, or None if
            the event was not relevant for Telegram notifications
        """
        from openhands.core.schema.agent import AgentState
        from openhands.events.action import Action
        from openhands.events.observation import AgentStateChangedObservation

        conv_id_str = str(conversation_id)

        try:
            # Check if this is a confirmation-requested event
            if isinstance(event, Action):
                if hasattr(event, 'confirmation_state'):
                    confirmation_state = getattr(event, 'confirmation_state', None)
                    if confirmation_state == 'awaiting_confirmation':
                        # Get the external user for this conversation
                        external_user_id = (
                            await self.messaging_service.get_user_for_conversation(
                                conv_id_str
                            )
                        )
                        if external_user_id:
                            logger.info(
                                f'Requesting Telegram confirmation for action '
                                f'in conversation {conv_id_str}'
                            )
                            # Request confirmation via Telegram
                            # Note: This is handled by the MessagingService
                            # which coordinates with the TelegramIntegration
                            pass

            # Check if this is a task completion event
            elif isinstance(event, AgentStateChangedObservation):
                agent_state = getattr(event, 'agent_state', None)
                if agent_state in (
                    AgentState.FINISHED,
                    AgentState.ERROR,
                    AgentState.REJECTED,
                ):
                    external_user_id = (
                        await self.messaging_service.get_user_for_conversation(
                            conv_id_str
                        )
                    )
                    if external_user_id:
                        reason = getattr(event, 'reason', '')
                        await self.messaging_service.send_task_result(
                            external_user_id=external_user_id,
                            conversation_id=conv_id_str,
                            state=agent_state,
                            reason=reason,
                        )
                        logger.info(
                            f'Sent task result to Telegram user {external_user_id} '
                            f'for conversation {conv_id_str}'
                        )

            return EventCallbackResult(
                status=EventCallbackResultStatus.SUCCESS,
                event_callback_id=callback.id,
                event_id=event.id,
                conversation_id=conversation_id,
            )

        except Exception as e:
            logger.exception(f'Error processing event for Telegram callback: {e}')
            return EventCallbackResult(
                status=EventCallbackResultStatus.ERROR,
                event_callback_id=callback.id,
                event_id=event.id,
                conversation_id=conversation_id,
                detail=str(e),
            )
