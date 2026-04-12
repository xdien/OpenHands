"""Messaging Configuration Models.

This module defines the configuration models for the messaging interface,
including base configuration and provider-specific configurations.
"""

from enum import Enum
from typing import Any

from pydantic import Field, SecretStr, field_validator

from openhands.sdk.utils.models import OpenHandsModel


class MessagingProviderType(str, Enum):
    """Enum of supported messaging providers."""

    TELEGRAM = 'telegram'
    # Future providers can be added here:
    # DISCORD = "discord"
    # SLACK = "slack"
    # MATRIX = "matrix"


class MessagingConfig(OpenHandsModel):
    """Base configuration for messaging integrations.

    This configuration controls the overall messaging interface settings,
    including which provider to use and which users are allowed to connect.

    Attributes:
        enabled: Whether the messaging interface is enabled
        provider: The messaging provider type to use
        allowed_user_ids: List of allowed external user IDs
            (e.g., Telegram chat IDs as strings)
        provider_config: Provider-specific configuration dictionary
            (e.g., bot_token, webhook_url for Telegram)
    """

    enabled: bool = Field(default=False, description='Enable messaging interface')
    provider: MessagingProviderType = Field(
        default=MessagingProviderType.TELEGRAM, description='Messaging provider type'
    )
    allowed_user_ids: list[str] = Field(
        default_factory=list,
        description='List of allowed external user IDs (e.g., Telegram chat IDs)',
    )

    @field_validator('allowed_user_ids', mode='before')
    @classmethod
    def _parse_allowed_user_ids(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                import json

                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
            return [x.strip() for x in v.split(',') if x.strip()]
        return v

    provider_config: dict[str, str] | None = Field(
        default=None,
        description='Provider-specific configuration (bot_token, webhook_url, etc.)',
    )

    @field_validator('provider_config', mode='before')
    @classmethod
    def _stringify_provider_config_values(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return {k: str(val) for k, val in v.items()}
        return v

    def get_telegram_config(self) -> 'TelegramConfig':
        """Extract Telegram configuration from provider_config.

        Returns:
            TelegramConfig object parsed from provider_config

        Raises:
            ValueError: If provider is not TELEGRAM or provider_config is invalid
        """
        raise RuntimeError('Telegram integration has been removed')


class TelegramConfig:
    """Telegram-specific configuration (deprecated).

    Telegram integration has been removed. Please use enterprise integrations
    (Slack, Discord) instead.
    """

    pass

    @property
    def is_webhook_mode(self) -> bool:
        """Check if the bot is configured for webhook mode.

        Returns:
            True if webhook_url is set, False for polling mode
        """
        return self.webhook_url is not None
