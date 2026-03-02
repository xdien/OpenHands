"""Telegram Integration Implementation.

This module provides the Telegram Bot integration for OpenHands.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from openhands.messaging.base import BaseMessagingIntegration
from openhands.messaging.config import MessagingProviderType, TelegramConfig
from openhands.messaging.stores.confirmation_store import (
    ConfirmationStatus,
    PendingConfirmation,
)

if TYPE_CHECKING:
    from openhands.messaging.messaging_service import MessagingService

logger = logging.getLogger(__name__)


class TelegramIntegration(BaseMessagingIntegration):
    """Telegram Bot integration for OpenHands.

    This integration allows OpenHands to communicate with users via Telegram,
    supporting both polling and webhook modes for receiving updates.

    Features:
    - Send text messages to users
    - Request action confirmations via inline keyboards
    - Handle incoming messages and commands
    - Support for both polling and webhook modes

    Attributes:
        config: Telegram configuration
        allowed_user_ids: Set of authorized Telegram chat IDs
        messaging_service: Reference to parent MessagingService
        _app: Telegram Application instance
        _pending_confirmations: Dict of confirmation_id -> PendingConfirmation
        _confirmation_events: Dict of confirmation_id -> asyncio.Event
    """

    def __init__(
        self,
        config: TelegramConfig,
        allowed_user_ids: set[str],
        messaging_service: 'MessagingService',
    ):
        """Initialize the Telegram integration.

        Args:
            config: Telegram configuration containing bot token and settings
            allowed_user_ids: Set of authorized Telegram chat IDs
            messaging_service: Reference to parent MessagingService
        """
        super().__init__(allowed_user_ids, messaging_service)
        self.config = config
        self._app: Any | None = None
        self._pending_confirmations: dict[str, PendingConfirmation] = {}
        self._confirmation_events: dict[str, asyncio.Event] = {}

    @property
    def name(self) -> str:
        """Return the integration name."""
        return 'telegram'

    @property
    def provider_type(self) -> MessagingProviderType:
        """Return the provider type."""
        return MessagingProviderType.TELEGRAM

    async def start(self) -> None:
        """Start the Telegram bot.

        This method initializes the Telegram Bot API connection and
        starts listening for updates via polling or webhook.
        """
        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            logger.error(
                'python-telegram-bot is not installed. '
                'Install it with: pip install python-telegram-bot'
            )
            raise

        bot_token = self.config.bot_token.get_secret_value()

        # Build the application
        self._app = Application.builder().token(bot_token).build()

        # Register command handlers
        self._app.add_handler(CommandHandler('start', self._cmd_start))
        self._app.add_handler(CommandHandler('help', self._cmd_help))
        self._app.add_handler(CommandHandler('status', self._cmd_status))
        self._app.add_handler(CommandHandler('cancel', self._cmd_cancel))

        # Register message handler for non-command messages
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        # Register callback query handler for inline keyboard buttons
        self._app.add_handler(
            CallbackQueryHandler(self._on_callback_query, pattern=r'^(confirm|reject):')
        )

        # Start the bot
        if self.config.is_webhook_mode:
            # Webhook mode
            webhook_url = self.config.webhook_url
            if webhook_url:
                await self._app.bot.set_webhook(webhook_url)
                logger.info(f'Telegram bot started in webhook mode: {webhook_url}')
        else:
            # Polling mode
            await self._app.initialize()
            await self._app.start()
            updater = self._app.updater
            if updater:
                await updater.start_polling(poll_interval=self.config.poll_interval)
            logger.info('Telegram bot started in polling mode')

    async def stop(self) -> None:
        """Stop the Telegram bot gracefully."""
        if self._app:
            if self.config.is_webhook_mode:
                await self._app.bot.delete_webhook()
            else:
                updater = self._app.updater
                if updater:
                    await updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info('Telegram bot stopped')

    async def send_message(
        self, external_user_id: str, message: str, **kwargs: Any
    ) -> bool:
        """Send a message to a Telegram user.

        Args:
            external_user_id: Telegram chat_id
            message: Message content to send
            **kwargs: Additional arguments for bot.send_message()

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self._app or not self._app.bot:
            logger.error('Telegram bot not initialized')
            return False

        try:
            # Truncate message if needed
            max_length = self.config.max_message_length
            if len(message) > max_length:
                message = message[: max_length - 50] + '\n\n... (truncated)'

            await self._app.bot.send_message(
                chat_id=external_user_id, text=message, **kwargs
            )
            return True
        except Exception as e:
            logger.error(f'Failed to send Telegram message to {external_user_id}: {e}')
            return False

    async def request_confirmation(
        self,
        external_user_id: str,
        action_type: str,
        action_details: str,
        action_content: str,
        conversation_id: str,
        timeout_seconds: int = 300,
    ) -> ConfirmationStatus:
        """Request user confirmation via Telegram inline keyboard.

        Args:
            external_user_id: Telegram chat_id
            action_type: Type of action requiring confirmation
            action_details: Human-readable action description
            action_content: Full action content for review
            conversation_id: OpenHands conversation ID
            timeout_seconds: How long to wait for confirmation

        Returns:
            ConfirmationStatus indicating user's response
        """
        import uuid

        confirmation_id = str(uuid.uuid4())

        # Create pending confirmation
        pending = PendingConfirmation(
            id=confirmation_id,
            conversation_id=conversation_id,
            external_user_id=external_user_id,
            action_type=action_type,
            action_details=action_details,
            action_content=action_content,
            status=ConfirmationStatus.PENDING,
        )
        pending.set_expiration(timeout_seconds)

        # Store confirmation and create wait event
        self._pending_confirmations[confirmation_id] = pending
        event = asyncio.Event()
        self._confirmation_events[confirmation_id] = event

        # Build inline keyboard with Approve/Reject buttons
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    '✅ Approve', callback_data=f'confirm:{confirmation_id}'
                ),
                InlineKeyboardButton(
                    '❌ Reject', callback_data=f'reject:{confirmation_id}'
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format message with Markdown
        message_text = (
            f'⚠️ *Action Confirmation Required*\n\n'
            f'*Action Type:* {action_type}\n'
            f'*Description:* {action_details}\n\n'
            f'*Details:*\n```\n{action_content}\n```\n\n'
            f'⏰ This request will expire in {timeout_seconds // 60} minutes.'
        )

        if not self._app or not self._app.bot:
            return ConfirmationStatus.EXPIRED

        try:
            # Send confirmation request
            msg = await self._app.bot.send_message(
                chat_id=external_user_id,
                text=message_text,
                parse_mode='Markdown',
                reply_markup=reply_markup,
            )
            pending.callback_message_id = msg.message_id

            # Wait for response with timeout
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                pending.status = ConfirmationStatus.EXPIRED
                await self.send_message(
                    external_user_id, '⏰ Confirmation request expired.'
                )
                return ConfirmationStatus.EXPIRED

            return pending.status

        finally:
            # Cleanup
            self._pending_confirmations.pop(confirmation_id, None)
            self._confirmation_events.pop(confirmation_id, None)

    async def _cmd_start(self, update: Any, context: Any) -> None:
        """Handle /start command."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        chat_id = str(update.effective_chat.id)

        if chat_id not in self.allowed_user_ids:
            await update.message.reply_text(
                '❌ You are not authorized to use this bot. '
                'Please contact the administrator.'
            )
            return

        await update.message.reply_text(
            '👋 Welcome to OpenHands!\n\n'
            "Send me a message with your task, and I'll execute it.\n"
            'Use /help for more information.'
        )

    async def _cmd_help(self, update: Any, context: Any) -> None:
        """Handle /help command."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        await update.message.reply_text(
            '📖 *OpenHands Bot Help*\n\n'
            '*Commands:*\n'
            '/start - Start the bot\n'
            '/help - Show this help message\n'
            '/status - Check current task status\n'
            '/cancel - Cancel current task\n\n'
            '*Usage:*\n'
            'Just send a message with your task, e.g.:\n'
            '"Fix the CSS on the homepage"',
            parse_mode='Markdown',
        )

    async def _cmd_status(self, update: Any, context: Any) -> None:
        """Handle /status command."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        chat_id = str(update.effective_chat.id)

        # Get conversation status from messaging_service
        status = await self.messaging_service.get_conversation_status(
            external_user_id=chat_id
        )
        await update.message.reply_text(status)

    async def _cmd_cancel(self, update: Any, context: Any) -> None:
        """Handle /cancel command."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        chat_id = str(update.effective_chat.id)

        await self.messaging_service.cancel_conversation(external_user_id=chat_id)
        await update.message.reply_text('⏹️ Task cancelled.')

    async def _on_message(self, update: Any, context: Any) -> None:
        """Handle incoming text messages."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.message:
            return

        chat_id = str(update.effective_chat.id)
        text = update.message.text

        if chat_id not in self.allowed_user_ids:
            logger.warning(f'Unauthorized message attempt from {chat_id}')
            return

        await self.messaging_service.handle_incoming_message(
            external_user_id=chat_id,
            message_text=text,
            message_metadata={
                'chat_id': chat_id,
                'message_id': update.message.message_id,
                'username': update.effective_chat.username,
                'timestamp': datetime.utcnow().isoformat(),
            },
        )

    async def _on_callback_query(self, update: Any, context: Any) -> None:
        """Handle inline keyboard callback queries for confirmations."""
        from telegram import Update as TGUpdate

        if not isinstance(update, TGUpdate) or not update.callback_query:
            return

        query = update.callback_query
        if not query.data:
            return

        # Parse callback data: "confirm:uuid" or "reject:uuid"
        parts = query.data.split(':', 1)
        if len(parts) != 2:
            await query.answer('Invalid callback data.')
            return

        action, confirmation_id = parts

        # Check if confirmation exists
        if confirmation_id not in self._pending_confirmations:
            await query.answer(
                'This confirmation request has expired or been processed.'
            )
            return

        pending = self._pending_confirmations[confirmation_id]

        # Check if already processed
        if pending.status != ConfirmationStatus.PENDING:
            await query.answer('This confirmation request has already been processed.')
            return

        # Update status based on user action
        if action == 'confirm':
            pending.status = ConfirmationStatus.CONFIRMED
            await query.answer('✅ Action approved!')
            if query.message:
                original_text = getattr(query.message, 'text', '')
                if original_text:
                    await query.edit_message_text(
                        text=f'{original_text}\n\n✅ *Approved*',
                        parse_mode='Markdown',
                    )
        elif action == 'reject':
            pending.status = ConfirmationStatus.REJECTED
            await query.answer('❌ Action rejected.')
            if query.message:
                original_text = getattr(query.message, 'text', '')
                if original_text:
                    await query.edit_message_text(
                        text=f'{original_text}\n\n❌ *Rejected*',
                        parse_mode='Markdown',
                    )

        # Signal the waiting task
        event = self._confirmation_events.get(confirmation_id)
        if event:
            event.set()

    async def handle_incoming_message(
        self, external_user_id: str, message_text: str, message_metadata: dict
    ) -> None:
        """Handle an incoming message from a Telegram user.

        This method delegates to the messaging service for processing.

        Args:
            external_user_id: Telegram chat_id
            message_text: The text content of the message
            message_metadata: Additional metadata about the message
        """
        await self.messaging_service.handle_incoming_message(
            external_user_id=external_user_id,
            message_text=message_text,
            message_metadata=message_metadata,
        )
