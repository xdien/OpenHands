"""Discord Gateway Bot for receiving mention events via WebSocket.

This module runs a discord.py bot that connects to Discord Gateway and
listens for MESSAGE_CREATE events (bot mentions). When a mention arrives,
it forwards the message to DiscordManager for processing.

This is SEPARATE from the Interactions Endpoint (/on-event) which only
handles slash commands and button interactions. Gateway connection is
required to receive regular message mentions (on_message events).

Usage:
    # Start as background task in the ASGI lifespan
    from integrations.discord.discord_gateway_bot import start_discord_gateway_bot
    asyncio.create_task(start_discord_gateway_bot())
"""

import asyncio
import os
import sys

import discord
from discord.ext import commands

from openhands.core.logger import openhands_logger as logger

# Read from env (already loaded by saas_server.py via dotenv)
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID', '')


def _create_bot() -> commands.Bot:
    """Create and configure the Discord bot with required intents."""
    intents = discord.Intents.default()
    intents.message_content = True   # Required: Privileged Intent – must be enabled in Developer Portal
    intents.messages = True
    intents.guilds = True
    intents.members = False  # Not needed

    bot = commands.Bot(command_prefix='!', intents=intents)
    return bot


async def _run_bot(bot: commands.Bot, discord_manager) -> None:
    """Run the Discord gateway bot, reconnecting on transient errors."""
    if not DISCORD_BOT_TOKEN:
        logger.warning('discord_gateway_bot: DISCORD_BOT_TOKEN not set, gateway bot disabled')
        return

    @bot.event
    async def on_ready():
        logger.info(f'discord_gateway_bot: Logged in as {bot.user} (ID: {bot.user.id})')

    @bot.event
    async def on_message(message: discord.Message):
        """Handle incoming messages and forward bot mentions to DiscordManager."""
        # Ignore messages from the bot itself
        if message.author.bot:
            return

        # Only process messages that mention the bot
        if bot.user not in message.mentions:
            return

        logger.info(
            f'discord_gateway_bot: mention received from {message.author} '
            f'in channel {message.channel.id}'
        )

        # Import here to avoid circular imports at module load time
        from integrations.models import Message, SourceType
        from openhands.server.shared import sio

        # Deduplicate via Redis (same key as on_event webhook handler)
        try:
            redis = sio.manager.redis
            key = f'discord_msg:{message.id}'
            created = await redis.set(key, 1, nx=True, ex=60)
            if not created:
                logger.info('discord_gateway_bot: duplicate message, skipping')
                return
        except Exception as e:
            logger.warning(f'discord_gateway_bot: redis dedup failed: {e}')

        # Build payload matching the format expected by DiscordManager
        message_payload = {
            'discord_user_id': str(message.author.id),
            'channel_id': message.channel.id,
            'message_id': message.id,
            'thread_id': (
                message.channel.id
                if isinstance(message.channel, discord.Thread)
                else None
            ),
            'guild_id': message.guild.id if message.guild else 0,
            'user_msg': message.content,
        }

        msg = Message(source=SourceType.DISCORD, message=message_payload)

        # Process asynchronously so the gateway event loop is not blocked
        asyncio.create_task(discord_manager.receive_message(msg))

    @bot.event
    async def on_error(event, *args, **kwargs):
        logger.exception(f'discord_gateway_bot: error in event {event}')

    # Run the bot (this blocks until the bot is stopped)
    await bot.start(DISCORD_BOT_TOKEN)


async def start_discord_gateway_bot() -> None:
    """Start the Discord gateway bot in a persistent loop with auto-reconnect.

    This function is designed to be run as a long-lived asyncio Task from
    the ASGI lifespan startup handler.
    """
    if not DISCORD_BOT_TOKEN:
        logger.warning('discord_gateway_bot: No DISCORD_BOT_TOKEN – gateway bot will not start')
        return

    # Import here to avoid premature loading before .env is parsed
    from integrations.discord.discord_manager import DiscordManager
    from server.auth.token_manager import TokenManager

    token_manager = TokenManager()
    discord_manager = DiscordManager(token_manager)

    retry_delay = 5  # seconds before reconnect
    while True:
        bot = _create_bot()
        try:
            logger.info('discord_gateway_bot: starting…')
            await _run_bot(bot, discord_manager)
        except discord.LoginFailure as e:
            logger.error(f'discord_gateway_bot: login failed (check token): {e}')
            break  # Don't retry on auth failure
        except (discord.ConnectionClosed, discord.GatewayNotFound, OSError) as e:
            logger.warning(f'discord_gateway_bot: connection lost ({e}), retrying in {retry_delay}s…')
        except asyncio.CancelledError:
            logger.info('discord_gateway_bot: cancelled, shutting down')
            break
        except Exception as e:
            logger.exception(f'discord_gateway_bot: unexpected error: {e}')
        finally:
            try:
                await bot.close()
            except Exception:
                pass

        await asyncio.sleep(retry_delay)
