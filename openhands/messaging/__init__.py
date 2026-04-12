"""Messaging Interface for OpenHands.

This module provides a generic messaging interface that allows OpenHands
to communicate with users via external messaging platforms.

The main components are:
- BaseMessagingIntegration: Abstract base class for all messaging integrations
- MessagingService: Main service that orchestrates messaging integrations
"""

from openhands.messaging.base import BaseMessagingIntegration
from openhands.messaging.config import (
    MessagingConfig,
    MessagingProviderType,
)
from openhands.messaging.messaging_service import MessagingService

__all__ = [
    'BaseMessagingIntegration',
    'MessagingService',
    'MessagingConfig',
    'MessagingProviderType',
]
