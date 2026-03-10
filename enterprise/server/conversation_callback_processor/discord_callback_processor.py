"""Discord callback processor for handling conversation events.

This module processes events from OpenHands conversations and sends
updates back to Discord channels.
"""

import asyncio
from uuid import UUID

from integrations.utils import get_final_agent_observation
from server.conversation_callback_processor.base_callback_processor import (
    BaseCallbackProcessor,
)
from server.logger import logger

from openhands.core.schema.agent import AgentState
from openhands.events.event import Event
from openhands.events.observation.agent import AgentStateChangedObservation
from openhands.events.observation.observation import Observation
from openhands.server.shared import sio


class DiscordCallbackProcessor(BaseCallbackProcessor):
    """Processes conversation events and sends updates to Discord.

    This callback processor is registered when a new conversation is
    started from Discord. It listens for conversation events and sends
    relevant updates back to the Discord channel.
    """

    def __init__(
        self,
        discord_user_id: str,
        channel_id: int,
        message_id: int,
        thread_id: int | None,
        guild_id: int,
    ):
        """Initialize the Discord callback processor.

        Args:
            discord_user_id: The Discord user ID
            channel_id: The Discord channel ID
            message_id: The original message ID
            thread_id: The thread ID (if in a thread)
            guild_id: The Discord guild (server) ID
        """
        self.discord_user_id = discord_user_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.thread_id = thread_id
        self.guild_id = guild_id
        self._finished = False

    async def __call__(
        self, conversation_id: str, event: Event, event_dict: dict
    ) -> None:
        """Process a conversation event.

        Args:
            conversation_id: The conversation ID
            event: The event object
            event_dict: The event as a dictionary
        """
        # Skip if already finished
        if self._finished:
            return

        # Handle agent state changes
        if isinstance(event, AgentStateChangedObservation):
            await self._handle_agent_state_change(
                conversation_id, event, event_dict
            )

        # Handle other observations that should be sent to Discord
        elif isinstance(event, Observation):
            await self._handle_observation(conversation_id, event, event_dict)

    async def _handle_agent_state_change(
        self, conversation_id: str, event: AgentStateChangedObservation, event_dict: dict
    ) -> None:
        """Handle agent state change events.

        Args:
            conversation_id: The conversation ID
            event: The state change event
            event_dict: The event as a dictionary
        """
        state = event.agent_state
        logger.info(
            f'[Discord] Agent state changed to {state} for conversation {conversation_id}'
        )

        if state == AgentState.FINISHED:
            await self._send_final_message(conversation_id, event_dict)
            self._finished = True
        elif state == AgentState.AWAITING_USER_INPUT:
            await self._send_awaiting_input_message(conversation_id)
        elif state == AgentState.RUNNING:
            # Agent is working, maybe send a status update
            logger.info(f'[Discord] Agent is running for conversation {conversation_id}')

    async def _handle_observation(
        self, conversation_id: str, event: Observation, event_dict: dict
    ) -> None:
        """Handle observation events.

        Args:
            conversation_id: The conversation ID
            event: The observation event
            event_dict: The event as a dictionary
        """
        # Only send important observations to avoid spamming Discord
        # This can be customized based on needs
        pass

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
        """Send a message to Discord.

        Args:
            message: The message to send
        """
        logger.info(
            f'[Discord] Sending message to channel {self.channel_id}: {message[:100]}...'
        )

        # In production, this would use the Discord bot client
        # For now, we log the message
        # TODO: Implement actual Discord message sending via discord.py
        pass

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
