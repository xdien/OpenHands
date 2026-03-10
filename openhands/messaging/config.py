"""Messaging Configuration Models.

This module defines the configuration models for the messaging interface,
including base configuration and provider-specific configurations.
"""

from enum import Enum

from pydantic import Field, SecretStr

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
    provider_config: dict | None = Field(
        default=None,
        description='Provider-specific configuration (bot_token, webhook_url, etc.)',
    )

    def get_telegram_config(self) -> 'TelegramConfig':
        """Extract Telegram configuration from provider_config.

        Returns:
            TelegramConfig object parsed from provider_config

        Raises:
            ValueError: If provider is not TELEGRAM or provider_config is invalid
        """
        if self.provider != MessagingProviderType.TELEGRAM:
            raise ValueError(f'Provider is {self.provider}, not TELEGRAM')

        if not self.provider_config:
            raise ValueError('provider_config is not set')

        return TelegramConfig.model_validate(self.provider_config)


class TelegramConfig(OpenHandsModel):
    """Telegram-specific configuration.

    This configuration controls the Telegram Bot integration settings.

    Attributes:
        bot_token: Telegram Bot Token obtained from @BotFather
        webhook_url: Optional webhook URL for receiving updates.
            If not set, the bot will use polling mode.
        poll_interval: Polling interval in seconds (only for polling mode)
        max_message_length: Maximum message length before truncation
    """

    bot_token: SecretStr = Field(..., description='Telegram Bot Token from @BotFather')
    webhook_url: str | None = Field(
        default=None, description='Optional webhook URL. If not set, uses polling mode.'
    )
    poll_interval: int = Field(
        default=1,
        ge=0,
        le=10,
        description='Polling interval in seconds (only for polling mode)',
    )
    max_message_length: int = Field(
        default=4096, ge=1, description='Max message length before truncation'
    )

    @property
    def is_webhook_mode(self) -> bool:
        """Check if the bot is configured for webhook mode.

        Returns:
            True if webhook_url is set, False for polling mode
        """
        return self.webhook_url is not None
