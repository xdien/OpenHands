#!/usr/bin/env python3
"""
Discord Bot Client for OpenHands
This script runs a Discord bot that connects to Discord Gateway
to show online status and receive events in real-time.

Note: This is separate from the webhook-based interaction handler.
The webhook handles slash commands and interactions, while this
bot client handles real-time events and shows online status.
"""

import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands

# Configure logging
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('discord-bot')

# Get configuration from environment
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID')
WEBHOOK_URL = os.environ.get(
    'WEBHOOK_URL', 'https://canthotouring.com/discord/on-event'
)

if not DISCORD_BOT_TOKEN:
    logger.error('DISCORD_BOT_TOKEN environment variable is not set!')
    sys.exit(1)

# Set up intents - REQUIRED for bot to receive message events
intents = discord.Intents.default()
intents.message_content = True  # Required to read message content
intents.members = True  # Required for member events
intents.presences = True  # Required for presence events

# Create bot instance
bot = commands.Bot(
    command_prefix='!',  # Prefix for traditional commands (not used for slash commands)
    intents=intents,
    help_command=None,  # Disable default help command
)


@bot.event
async def on_ready():
    """Called when the bot has connected to Discord and is ready."""
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guild(s)')
    logger.info('Bot is now online and ready!')

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} slash command(s)')
    except Exception as e:
        logger.error(f'Failed to sync slash commands: {e}')


@bot.event
async def on_message(message):
    """Called when a message is received in any channel the bot can see."""
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Check if bot is mentioned
    if bot.user.mentioned_in(message):
        logger.info(
            f'Bot mentioned by {message.author} in {message.guild}/{message.channel}'
        )
        logger.info(f'Message content: {message.content}')

        # For now, just acknowledge the mention
        # In production, this would forward to the webhook or handle directly
        try:
            await message.reply(
                f'👋 Hello {message.author.mention}! I received your message.\n\n'
                f'Note: Full OpenHands integration requires account linking. '
                f'Please visit the web interface to get started.'
            )
        except discord.errors.Forbidden:
            logger.warning(f'Cannot send message to {message.channel}')
        except Exception as e:
            logger.error(f'Error sending reply: {e}')

    # Process commands (not used for slash commands)
    await bot.process_commands(message)


@bot.event
async def on_interaction(interaction):
    """Called when an interaction is received (button clicks, etc.)."""
    logger.info(f'Interaction received from {interaction.user}: {interaction.type}')


# Slash command for testing
@bot.tree.command(name='ping', description='Test command to check if bot is working')
async def ping(interaction: discord.Interaction):
    """Simple ping command to test bot functionality."""
    await interaction.response.send_message('🏓 Pong! Bot is working!', ephemeral=True)


@bot.tree.command(name='help', description='Show help information')
async def help_command(interaction: discord.Interaction):
    """Show help information about the bot."""
    embed = discord.Embed(
        title='🤖 OpenHands Discord Bot',
        description='AI-powered software engineer assistant',
        color=discord.Color.blue(),
    )
    embed.add_field(
        name='How to use',
        value='Mention me with `@OpenHandsBot` followed by your request to start a conversation!',
        inline=False,
    )
    embed.add_field(
        name='Commands',
        value='/ping - Test if bot is working\n/help - Show this help message',
        inline=False,
    )
    embed.add_field(
        name='Web Interface',
        value='For full functionality, please visit the web interface to link your account.',
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


async def main():
    """Main function to run the bot."""
    logger.info('Starting Discord Bot Client...')
    logger.info(
        f'Intents: message_content={intents.message_content}, members={intents.members}'
    )

    try:
        async with bot:
            await bot.start(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        logger.error('Failed to login! Please check your DISCORD_BOT_TOKEN')
        sys.exit(1)
    except Exception as e:
        logger.error(f'Unexpected error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
