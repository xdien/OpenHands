"""Telegram Integration for OpenHands.

This module provides the Telegram Bot integration for OpenHands,
allowing users to interact with OpenHands via Telegram.

Features:
- Send and receive messages
- Request action confirmations via inline keyboards
- Command handlers (/start, /help, /status, /cancel)
- Support for both polling and webhook modes
"""

from openhands.messaging.telegram.telegram_integration import TelegramIntegration
from openhands.messaging.telegram.telegram_callback_processor import (
    TelegramCallbackProcessor,
)

__all__ = [
    "TelegramIntegration",
    "TelegramCallbackProcessor",
]
