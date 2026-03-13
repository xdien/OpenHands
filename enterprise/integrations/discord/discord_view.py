"""Discord view implementations for handling different conversation types.

This module provides view classes for Discord integration:
- DiscordNewConversationView: Handle new conversations from Discord mentions
- DiscordUpdateExistingConversationView: Handle follow-up messages in threads
- DiscordFactory: Factory for creating views from payloads
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from integrations.models import Message
from integrations.resolver_context import ResolverUserContext
from integrations.discord.discord_types import (
    DiscordMessageView,
    DiscordViewInterface,
    StartingConvoException,
)
from integrations.utils import (
    CONVERSATION_URL,
    get_final_agent_observation,
)
from jinja2 import Environment
from storage.discord_user import DiscordUser

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartRequest,
    SendMessageRequest,
)
from openhands.app_server.config import get_app_conversation_service
from openhands.app_server.sandbox.sandbox_models import SandboxStatus
from openhands.app_server.services.injector import InjectorState
from openhands.app_server.user.specifiy_user_context import USER_CONTEXT_ATTR
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema.agent import AgentState
from openhands.events.action import MessageAction
from openhands.events.serialization.event import event_to_dict
from openhands.integrations.provider import ProviderHandler, ProviderType
from openhands.sdk import TextContent
from openhands.server.services.conversation_service import (
    create_new_conversation,
    setup_init_conversation_settings,
)
from openhands.server.shared import ConversationStoreImpl, config, conversation_manager
from openhands.server.user_auth.user_auth import UserAuth
from openhands.storage.data_models.conversation_metadata import (
    ConversationTrigger,
)
from openhands.utils.async_utils import GENERAL_TIMEOUT

# =================================================
# SECTION: Discord view types
# =================================================

CONTEXT_LIMIT = 21


@dataclass
class DiscordNewConversationView(DiscordViewInterface):
    """View for creating new conversations from Discord mentions."""

    bot_token: str
    user_msg: str
    discord_user_id: str
    discord_to_openhands_user: DiscordUser
    saas_user_auth: UserAuth
    channel_id: int
    message_id: int
    thread_id: int | None
    selected_repo: str | None
    should_extract: bool
    send_summary_instruction: bool
    conversation_id: str
    guild_id: int
    v1_enabled: bool = False  # Discord integration starts with V0

    def _get_initial_prompt(self, text: str, mentions: list[dict]) -> str:
        """Remove bot mention from the message text."""
        # Remove <@bot_id> or <@!bot_id> mentions
        for mention in mentions:
            if mention.get('id'):
                text = text.replace(f'<@{mention["id"]}>', '')
                text = text.replace(f'<@!{mention["id"]}>', '')
        return text.strip()

    async def _get_instructions(self, jinja_env: Environment) -> tuple[str, str]:
        """Get instructions for the conversation from Discord message context."""
        user_info: DiscordUser = self.discord_to_openhands_user

        # For Discord, we'll use a simpler approach - just the user message
        # Discord API doesn't have the same threading model as Slack
        user_message = self.user_msg

        conversation_instructions = ''

        # If we have thread context, we could fetch it here
        # For now, we'll use a simple template
        conversation_instructions_template = jinja_env.get_template(
            'user_message_conversation_instructions.j2'
        )
        conversation_instructions = conversation_instructions_template.render(
            messages=[self.user_msg],
            username=user_info.discord_username,
            conversation_url=CONVERSATION_URL,
        )

        return user_message, conversation_instructions

    def _verify_necessary_values_are_set(self):
        if not self.selected_repo:
            raise ValueError(
                'Attempting to start conversation without confirming selected repo from user'
            )

    async def save_discord_convo(self):
        """Save the Discord conversation mapping."""
        if self.discord_to_openhands_user:
            user_info: DiscordUser = self.discord_to_openhands_user

            logger.info(
                'Create discord conversation',
                extra={
                    'channel_id': self.channel_id,
                    'conversation_id': self.conversation_id,
                    'keycloak_user_id': user_info.keycloak_user_id,
                    'org_id': user_info.org_id,
                    'parent_id': self.thread_id or self.message_id,
                },
            )
            # TODO: Implement DiscordConversation store similar to SlackConversation
            # discord_conversation = DiscordConversation(
            #     conversation_id=self.conversation_id,
            #     channel_id=self.channel_id,
            #     keycloak_user_id=user_info.keycloak_user_id,
            #     org_id=user_info.org_id,
            #     parent_id=self.thread_id or self.message_id,
            # )
            # await discord_conversation_store.create_discord_conversation(discord_conversation)

    async def create_or_update_conversation(self, jinja: Environment) -> str:
        """Create a new conversation."""
        self._verify_necessary_values_are_set()

        provider_tokens = await self.saas_user_auth.get_provider_tokens()
        user_secrets = await self.saas_user_auth.get_secrets()

        # Discord uses V0 conversation service for now
        await self._create_v0_conversation(jinja, provider_tokens, user_secrets)
        return self.conversation_id

    async def _create_v0_conversation(
        self, jinja: Environment, provider_tokens, user_secrets
    ) -> None:
        """Create conversation using the legacy V0 system."""
        user_instructions, conversation_instructions = await self._get_instructions(
            jinja
        )

        # Determine git provider from repository
        git_provider = None
        if self.selected_repo and provider_tokens:
            provider_handler = ProviderHandler(provider_tokens)
            repository = await provider_handler.verify_repo_provider(self.selected_repo)
            git_provider = repository.git_provider

        agent_loop_info = await create_new_conversation(
            user_id=self.discord_to_openhands_user.keycloak_user_id,
            git_provider_tokens=provider_tokens,
            selected_repository=self.selected_repo,
            selected_branch=None,
            initial_user_msg=user_instructions,
            conversation_instructions=(
                conversation_instructions if conversation_instructions else None
            ),
            image_urls=None,
            replay_json=None,
            conversation_trigger=ConversationTrigger.DISCORD,
            custom_secrets=user_secrets.custom_secrets if user_secrets else None,
            git_provider=git_provider,
        )

        self.conversation_id = agent_loop_info.conversation_id
        logger.info(f'[Discord]: Created V0 conversation: {self.conversation_id}')
        await self.save_discord_convo()

    def get_response_msg(self) -> str:
        """Get the response message to send back to Discord."""
        return f"I'm working on your request! You can follow the conversation here: {CONVERSATION_URL}/{self.conversation_id}"


@dataclass
class DiscordUpdateExistingConversationView(DiscordViewInterface):
    """View for updating existing conversations with follow-up messages."""

    bot_token: str
    user_msg: str
    discord_user_id: str
    discord_to_openhands_user: DiscordUser
    saas_user_auth: UserAuth
    channel_id: int
    message_id: int
    thread_id: int | None
    selected_repo: str | None
    should_extract: bool
    send_summary_instruction: bool
    conversation_id: str
    guild_id: int
    v1_enabled: bool = False

    async def _get_instructions(self, jinja_env: Environment) -> tuple[str, str]:
        """Get instructions from the follow-up message."""
        return self.user_msg, ''

    async def create_or_update_conversation(self, jinja: Environment) -> str:
        """Update an existing conversation with a new message."""
        # Get the existing conversation
        conversation_store = ConversationStoreImpl()
        conversation = await conversation_store.get_conversation(self.conversation_id)

        if not conversation:
            raise StartingConvoException(
                f'Conversation {self.conversation_id} not found'
            )

        # Send the follow-up message
        user_msg = self.user_msg

        async with conversation_manager.attach_to_conversation(
            self.conversation_id
        ) as attached_conversation:
            if attached_conversation:
                msg_action = MessageAction(content=user_msg)
                await attached_conversation.send_message(msg_action)
                logger.info(
                    f'[Discord]: Sent follow-up message to conversation {self.conversation_id}'
                )

        return self.conversation_id

    def get_response_msg(self) -> str:
        """Get the response message."""
        return f"Message received! Continuing the conversation here: {CONVERSATION_URL}/{self.conversation_id}"


class DiscordFactory:
    """Factory for creating Discord views from payloads."""

    @staticmethod
    async def create_discord_view_from_payload(
        message: Message,
        discord_user: DiscordUser | None,
        saas_user_auth: UserAuth | None,
    ) -> DiscordMessageView | DiscordViewInterface | None:
        """Create appropriate view from Discord payload.

        Args:
            message: The incoming message with Discord payload
            discord_user: The linked Discord user (if exists in database)
            saas_user_auth: The SaaS user auth (if Keycloak linked)

        Returns:
            DiscordViewInterface for fully authenticated users (has Keycloak),
            DiscordMessageView for:
                - Discord user exists but no Keycloak link
                - Discord user not found in database
            None if invalid payload
        """
        payload = message.message

        if not discord_user or not saas_user_auth:
            # Return basic message view for users without Keycloak integration
            # This allows Discord user to exist without OpenHands account
            return DiscordMessageView(
                bot_token='',  # Will be filled by from_payload
                discord_user_id=str(payload.get('discord_user_id', '')),
                channel_id=int(payload.get('channel_id', 0)),
                message_id=int(payload.get('message_id', 0)),
                thread_id=payload.get('thread_id'),
                guild_id=int(payload.get('guild_id', 0)),
            )

        # Determine if this is a new conversation or update
        conversation_id = payload.get('conversation_id')
        user_msg = payload.get('user_msg', '')
        selected_repo = payload.get('selected_repo')

        if conversation_id:
            # Update existing conversation
            return DiscordUpdateExistingConversationView(
                bot_token='',  # Will be filled later
                user_msg=user_msg,
                discord_user_id=str(payload.get('discord_user_id', '')),
                discord_to_openhands_user=discord_user,
                saas_user_auth=saas_user_auth,
                channel_id=int(payload.get('channel_id', 0)),
                message_id=int(payload.get('message_id', 0)),
                thread_id=payload.get('thread_id'),
                selected_repo=selected_repo,
                should_extract=True,
                send_summary_instruction=False,
                conversation_id=conversation_id,
                guild_id=int(payload.get('guild_id', 0)),
            )
        else:
            # New conversation
            return DiscordNewConversationView(
                bot_token='',  # Will be filled later
                user_msg=user_msg,
                discord_user_id=str(payload.get('discord_user_id', '')),
                discord_to_openhands_user=discord_user,
                saas_user_auth=saas_user_auth,
                channel_id=int(payload.get('channel_id', 0)),
                message_id=int(payload.get('message_id', 0)),
                thread_id=payload.get('thread_id'),
                selected_repo=selected_repo,
                should_extract=True,
                send_summary_instruction=False,
                conversation_id='',
                guild_id=int(payload.get('guild_id', 0)),
            )
