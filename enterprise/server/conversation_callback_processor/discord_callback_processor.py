"""Discord callback processor for handling conversation events.

This module processes events from OpenHands conversations and sends
updates back to Discord channels.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from integrations.utils import (
    extract_summary_from_conversation_manager,
    get_last_user_msg_from_conversation_manager,
    get_summary_instruction,
    append_conversation_footer,
)
from storage.conversation_callback import ConversationCallbackProcessor
from server.logger import logger

from openhands.core.schema.agent import AgentState
from openhands.events.action import MessageAction
from openhands.events.event import Event
from openhands.events.observation.agent import AgentStateChangedObservation
from openhands.server.shared import conversation_manager
from openhands.events.serialization.event import event_to_dict

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
    last_user_msg_id: int | None = None

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
            await self._handle_awaiting_user_input(callback, conversation_id)
        elif state == AgentState.RUNNING:
            logger.info(f'[Discord] Agent is running for conversation {conversation_id}')

    async def _handle_awaiting_user_input(
        self,
        callback: 'ConversationCallback',
        conversation_id: str,
    ) -> None:
        """Handle AWAITING_USER_INPUT state by requesting and sending summary.

        Args:
            callback: The conversation callback
            conversation_id: The conversation ID
        """
        try:
            logger.info(f'[Discord] Processing conversation {conversation_id}')

            # Get the summary instruction
            summary_instruction = get_summary_instruction()
            summary_event = event_to_dict(MessageAction(content=summary_instruction))

            # Prevent infinite loops for summary callback that always sends instructions when agent stops
            # We should not request summary if the last message is the summary request
            last_user_msg = await get_last_user_msg_from_conversation_manager(
                conversation_manager, conversation_id
            )

            # Check if we have any messages
            if len(last_user_msg) == 0:
                logger.info(
                    f'[Discord] No messages found for conversation {conversation_id}'
                )
                return

            # Get the ID of the last user message
            current_msg_id = last_user_msg[0].id if last_user_msg else None

            logger.info(
                '[Discord] last_user_msg',
                extra={
                    'last_user_msg': [m.content for m in last_user_msg],
                    'summary_instruction': summary_instruction,
                    'current_msg_id': current_msg_id,
                    'last_user_msg_id': self.last_user_msg_id,
                },
            )

            # Check if the message ID has changed
            if current_msg_id == self.last_user_msg_id:
                logger.info(
                    f'[Discord] Skipping processing as message ID has not changed: {current_msg_id}'
                )
                return

            # Update the last user message ID
            self.last_user_msg_id = current_msg_id

            # Update the processor in the callback and save to database
            callback.set_processor(self)

            logger.info(f'[Discord] Updated last_user_msg_id to {self.last_user_msg_id}')

            if last_user_msg[0].content == summary_instruction:
                # Extract the summary from the event store
                logger.info(
                    f'[Discord] Extracting summary for conversation {conversation_id}'
                )
                summary = await extract_summary_from_conversation_manager(
                    conversation_manager, conversation_id
                )

                # Send the summary to Discord
                asyncio.create_task(self._send_discord_message(summary))

                logger.info(f'[Discord] Summary sent for conversation {conversation_id}')
                return

            # Add the summary instruction to the event stream
            logger.info(
                f'[Discord] Sending summary instruction to conversation {conversation_id} {summary_event}'
            )
            await conversation_manager.send_event_to_conversation(
                conversation_id, summary_event
            )

            logger.info(
                f'[Discord] Sent summary instruction to conversation {conversation_id} {summary_event}'
            )

        except Exception:
            logger.error(
                '[Discord] Error processing conversation callback',
                exc_info=True,
                stack_info=True,
            )

    async def _send_final_message(
        self, conversation_id: str, event_dict: dict
    ) -> None:
        """Send a final message when the conversation is complete.

        Args:
            conversation_id: The conversation ID
            event_dict: The final event dictionary (not used, kept for compatibility)
        """
        try:
            # Extract the summary from the conversation manager
            logger.info(
                f'[Discord] Extracting final summary for conversation {conversation_id}'
            )
            summary = await extract_summary_from_conversation_manager(
                conversation_manager, conversation_id
            )

            # Send to Discord with conversation footer
            message = f"✅ Task completed!\n\n{summary}"
            await self._send_discord_message(message)

            logger.info(f'[Discord] Final summary sent for conversation {conversation_id}')
        except Exception:
            logger.error(
                '[Discord] Error sending final message',
                exc_info=True,
                stack_info=True,
            )

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
