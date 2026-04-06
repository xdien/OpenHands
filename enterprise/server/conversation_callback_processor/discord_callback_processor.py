"""Discord callback processor for handling conversation events.

This module processes events from OpenHands conversations and sends
updates back to Discord channels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from integrations.utils import get_final_agent_observation
from storage.conversation_callback import ConversationCallbackProcessor
from server.logger import logger

from openhands.core.schema.agent import AgentState
from openhands.events.observation.agent import AgentStateChangedObservation

if TYPE_CHECKING:
    from openhands.events.event import Event


class DiscordCallbackProcessor(ConversationCallbackProcessor):
    """Processes conversation events and sends updates to Discord.

    This callback processor is registered when a new conversation is
    started from Discord. It listens for conversation events and sends
    relevant updates back to the Discord channel.
    """

    # Use class-level type annotations like Slack
    discord_user_id: str
    channel_id: int
    message_id: int
    thread_id: int | None
    guild_id: int
    _finished: bool = False

    async def __call__(
        self,
        callback: 'ConversationCallback',
        observation: 'AgentStateChangedObservation',
    ) -> None:
        """Process a conversation event.

        Args:
            callback: The conversation callback
            observation: The AgentStateChangedObservation that triggered the callback
        """
        conversation_id = callback.conversation_id

        # Skip if already finished
        if self._finished:
            return

        # Handle agent state changes
        state = observation.agent_state
        logger.info(
            f'[Discord] Agent state changed to {state} for conversation {conversation_id}'
        )

        if state == AgentState.FINISHED:
            # Get the final message from the observation
            event_dict = observation.model_dump() if hasattr(observation, 'model_dump') else {}
            await self._send_final_message(conversation_id, event_dict)
            self._finished = True
        elif state == AgentState.AWAITING_USER_INPUT:
            await self._send_awaiting_input_message(conversation_id)
        elif state == AgentState.RUNNING:
            logger.info(f'[Discord] Agent is running for conversation {conversation_id}')

    async def _send_final_message(
        self, conversation_id: str, event_dict: dict
    ) -> None:
        """Send a final message when the conversation is complete.

        Args:
            conversation_id: The conversation ID
            event_dict: The final event dictionary
        """
        # Get the final observation
        final_message = get_final_agent_observation(event_dict)

        # Send to Discord
        message = f"✅ Task completed!\n\n{final_message}"
        await self._send_discord_message(message)

    async def _send_awaiting_input_message(self, conversation_id: str) -> None:
        """Send a message when the agent is waiting for user input.

        Args:
            conversation_id: The conversation ID
        """
        message = "🤔 I need more input from you. Please reply to continue."
        await self._send_discord_message(message)

    async def _send_discord_message(self, message: str) -> None:
        """Send a message to Discord channel via REST API.

        Args:
            message: The message to send
        """
        import httpx
        from server.constants import DISCORD_BOT_TOKEN

        if not DISCORD_BOT_TOKEN:
            logger.warning('[Discord] No bot token available to send message')
            return

        # Truncate to Discord limit
        if len(message) > 1990:
            message = message[:1990] + '…'

        target_channel = self.thread_id if self.thread_id else self.channel_id
        url = f'https://discord.com/api/v10/channels/{target_channel}/messages'
        headers = {
            'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
            'Content-Type': 'application/json',
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={'content': message}, headers=headers)
                if resp.status_code not in (200, 201):
                    logger.error(
                        f'[Discord] Failed to send message: {resp.status_code} {resp.text[:200]}'
                    )
                else:
                    logger.info(
                        f'[Discord] Message sent to channel {target_channel}'
                    )
        except Exception as e:
            logger.error(f'[Discord] Exception sending message: {e}')

    def to_dict(self) -> dict:
        """Serialize the processor to a dictionary for storage.

        Returns:
            Dictionary representation of the processor
        """
        return {
            'type': 'discord',
            'discord_user_id': self.discord_user_id,
            'channel_id': self.channel_id,
            'message_id': self.message_id,
            'thread_id': self.thread_id,
            'guild_id': self.guild_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DiscordCallbackProcessor':
        """Deserialize the processor from a dictionary.

        Args:
            data: Dictionary representation of the processor

        Returns:
            DiscordCallbackProcessor instance
        """
        return cls(
            discord_user_id=data['discord_user_id'],
            channel_id=data['channel_id'],
            message_id=data['message_id'],
            thread_id=data.get('thread_id'),
            guild_id=data['guild_id'],
        )
