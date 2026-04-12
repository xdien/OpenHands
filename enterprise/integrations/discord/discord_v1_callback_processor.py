import logging
from uuid import UUID

import httpx
from integrations.utils import get_summary_instruction
from integrations.v1_utils import handle_callback_error
from pydantic import Field

from openhands.agent_server.models import AskAgentRequest, AskAgentResponse
from openhands.app_server.event_callback.event_callback_models import (
    EventCallback,
    EventCallbackProcessor,
)
from openhands.app_server.event_callback.event_callback_result_models import (
    EventCallbackResult,
    EventCallbackResultStatus,
)
from openhands.app_server.event_callback.util import (
    get_agent_server_url_from_sandbox,
)
from openhands.sdk import Event
from openhands.sdk.event import ConversationStateUpdateEvent

from enterprise.storage.discord_conversation_store import DiscordConversationStore

_logger = logging.getLogger(__name__)


class DiscordV1CallbackProcessor(EventCallbackProcessor):
    """Callback processor for Discord V1 integration."""

    discord_view_data: dict[str, str | None] = Field(default_factory=dict)

    async def __call__(
        self,
        conversation_id: UUID,
        callback: EventCallback,
        event: Event,
    ) -> EventCallbackResult | None:
        """Process events for Discord V1 integration."""
        # Only handle ConversationStateUpdateEvent for execution_status
        if not isinstance(event, ConversationStateUpdateEvent):
            return None

        if event.key != 'execution_status':
            return None

        # Log ALL terminal states for monitoring (finished, error, stuck)
        _logger.info('[Discord V1] Callback agent state was %s', event)

        # Only request summary when execution has finished successfully
        if event.value != 'finished':
            return None

        try:
            summary = await self._request_summary(conversation_id)
            await self._post_summary_to_discord(summary)

            return EventCallbackResult(
                status=EventCallbackResultStatus.SUCCESS,
                event_callback_id=callback.id,
                event_id=event.id,
                conversation_id=conversation_id,
                detail=summary,
            )
        except Exception as e:
            await handle_callback_error(
                error=e,
                conversation_id=conversation_id,
                service_name='Discord',
                service_logger=_logger,
                can_post_error=True,  # Discord always attempts to post errors
                post_error_func=self._post_summary_to_discord,
            )

            return EventCallbackResult(
                status=EventCallbackResultStatus.ERROR,
                event_callback_id=callback.id,
                event_id=event.id,
                conversation_id=conversation_id,
                detail=str(e),
            )

    # -------------------------------------------------------------------------
    # Discord helpers
    # -------------------------------------------------------------------------

    async def _post_summary_to_discord(self, summary: str) -> None:
        """Post a summary message to the configured Discord channel."""
        channel_id = self.discord_view_data.get('channel_id')
        thread_id = self.discord_view_data.get('thread_id')

        if not channel_id:
            _logger.warning('[Discord V1] No channel_id in view data, skipping post')
            return

        # Get conversation_id from discord_view_data
        conversation_id = self.discord_view_data.get('conversation_id')
        if not conversation_id:
            _logger.warning('[Discord V1] No conversation_id in view data, skipping post')
            return

        # Look up the Discord conversation to get the channel info
        discord_conversation_store = DiscordConversationStore.get_instance()
        try:
            discord_convo = await discord_conversation_store.get_discord_conversation(
                conversation_id
            )
            if not discord_convo:
                _logger.warning(
                    '[Discord V1] No Discord conversation found for %s, skipping post',
                    conversation_id
                )
                return

            # TODO: Use Discord REST API to post the message
            # For now, just log the summary
            _logger.info(
                '[Discord V1] Would post summary to channel %s, thread %s: %s',
                discord_convo.discord_channel_id,
                discord_convo.discord_thread_id,
                summary[:100],  # Log first 100 chars
            )
        except Exception as e:
            _logger.error('[Discord V1] Failed to post summary: %s', e)

    async def _request_summary(self, conversation_id: UUID) -> str:
        """Request a summary from the agent for the conversation."""
        agent_server_url = await get_agent_server_url_from_sandbox(conversation_id)
        if not agent_server_url:
            raise RuntimeError(
                f'Could not get agent server URL for conversation {conversation_id}'
            )

        summary_instruction = get_summary_instruction()

        async with httpx.AsyncClient() as client:
            request = AskAgentRequest(
                conversation_id=str(conversation_id),
                message=summary_instruction,
            )
            response = await client.post(
                f'{agent_server_url.url}/api/ask',
                json=request.model_dump(mode='json'),
                headers={'Content-Type': 'application/json'},
                timeout=300.0,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f'Failed to get summary: {response.status_code} {response.text}'
                )

            result = AskAgentResponse(**response.json())
            return result.content
