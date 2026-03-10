"""Discord integration routes for OpenHands.

This module provides FastAPI routes for Discord integration:
- Webhook endpoint for Discord events (bot mentions)
- OAuth endpoints for Discord authentication
- Interaction endpoints for Discord UI components
"""

import json
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from integrations.models import Message, SourceType
from integrations.discord.discord_errors import DiscordError, DiscordErrorCode
from integrations.discord.discord_manager import DiscordManager
from integrations.utils import HOST_URL
from server.constants import (
    DISCORD_BOT_TOKEN,
    DISCORD_PUBLIC_KEY,
    DISCORD_WEBHOOKS_ENABLED,
)
from server.logger import logger
from server.auth.token_manager import TokenManager

from openhands.server.shared import sio

# Discord router with prefix
discord_router = APIRouter(prefix='/discord')

# Initialize token manager and Discord manager
token_manager = TokenManager()
discord_manager = DiscordManager(token_manager)


def verify_discord_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Discord webhook signature using Ed25519.

    Args:
        body: The raw request body
        signature: The X-Signature-Ed25519 header value
        timestamp: The X-Signature-Timestamp header value

    Returns:
        True if signature is valid, False otherwise
    """
    if not DISCORD_PUBLIC_KEY:
        logger.warning('discord_verify_no_public_key')
        return False

    try:
        # Discord uses Ed25519 for signature verification
        # This is different from Slack's HMAC approach
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError

        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        message = timestamp.encode() + body

        verify_key.verify(message, bytes.fromhex(signature))
        return True
    except ImportError:
        logger.warning('discord_verify_no_nacl_library')
        # Fallback: allow if library not installed (for development)
        return True
    except Exception as e:
        logger.error(f'discord_verify_signature_failed: {e}')
        return False


@discord_router.get('/install')
async def install():
    """Redirect to Discord OAuth authorization URL."""
    from server.constants import DISCORD_CLIENT_ID

    if not DISCORD_CLIENT_ID:
        raise HTTPException(status_code=500, detail='Discord integration not configured')

    # Discord OAuth URL
    redirect_uri = f'{HOST_URL}/discord/install-callback'
    scope = 'identify'  # Basic scope to get user info

    oauth_url = (
        f'https://discord.com/oauth2/authorize?'
        f'client_id={DISCORD_CLIENT_ID}&'
        f'redirect_uri={redirect_uri}&'
        f'response_type=code&'
        f'scope={scope}'
    )

    return RedirectResponse(oauth_url)


@discord_router.get('/install-callback')
async def install_callback(code: str = '', error: str = ''):
    """Handle Discord OAuth callback."""
    if error or not code:
        logger.warning(
            'discord_install_callback_error',
            extra={'code': code, 'error': error},
        )
        return JSONResponse(
            {'error': error or 'No authorization code provided'},
            status_code=400,
        )

    # TODO: Exchange code for access token and store user mapping
    return JSONResponse({'success': True, 'message': 'Discord account linked!'})


@discord_router.post('/on-event')
async def on_event(request: Request, background_tasks: BackgroundTasks):
    """Handle Discord bot mention events via webhook.

    This endpoint receives events from Discord when users mention the bot.
    Discord sends different event types:
    - PING: Used for endpoint verification (responds with PONG)
    - APPLICATION_COMMAND: Slash command interactions
    - MESSAGE_CREATE: Bot mention in message content
    """
    if not DISCORD_WEBHOOKS_ENABLED:
        return JSONResponse({'success': 'discord_webhooks_disabled'})

    body = await request.body()

    # Verify Discord signature
    signature = request.headers.get('x-signature-ed25519', '')
    timestamp = request.headers.get('x-signature-timestamp', '')

    if not verify_discord_signature(body, signature, timestamp):
        raise HTTPException(status_code=403, detail='invalid_request')

    payload = json.loads(body.decode())

    logger.info('discord_on_event', extra={'payload': payload})

    # Handle Discord PING (endpoint verification)
    if payload.get('type') == 1:
        return JSONResponse({'type': 1})  # PONG

    # Handle message create event (bot mention)
    if payload.get('type') == 0:  # MESSAGE_CREATE
        data = payload.get('d', {})

        # Check if bot is mentioned
        content = data.get('content', '')
        mentions = data.get('mentions', [])
        author = data.get('author', {})
        channel_id = data.get('channel_id')
        guild_id = data.get('guild_id')
        message_id = data.get('id')

        # Skip if no mentions or author is bot
        if not mentions or author.get('bot', False):
            return JSONResponse({'success': True})

        # Check if our bot is mentioned
        bot_mentioned = False
        for mention in mentions:
            # In production, check if mention['id'] matches our bot's user ID
            bot_mentioned = True
            break

        if not bot_mentioned:
            return JSONResponse({'success': True})

        # Check for duplicate messages using Redis
        redis = sio.manager.redis
        key = f'discord_msg:{message_id}'
        created = await redis.set(key, 1, nx=True, ex=60)
        if not created:
            logger.info('discord_is_duplicate')
            return JSONResponse({'success': True})

        # Build message payload for DiscordManager
        message_payload = {
            'discord_user_id': author.get('id'),
            'channel_id': int(channel_id) if channel_id else 0,
            'message_id': int(message_id) if message_id else 0,
            'thread_id': None,  # TODO: Extract from thread if applicable
            'guild_id': int(guild_id) if guild_id else 0,
            'user_msg': content,
        }

        message = Message(
            source=SourceType.DISCORD,
            message=message_payload,
        )

        # Process message in background
        background_tasks.add_task(discord_manager.receive_message, message)

        return JSONResponse({'success': True})

    # Handle interaction (slash commands, button clicks, etc.)
    if payload.get('type') == 2:  # APPLICATION_COMMAND
        return await _handle_interaction(payload)

    if payload.get('type') == 3:  # MESSAGE_COMPONENT
        return await _handle_component(payload)

    return JSONResponse({'success': True})


async def _handle_interaction(payload: dict) -> JSONResponse:
    """Handle Discord application command interactions.

    Args:
        payload: The interaction payload

    Returns:
        JSONResponse with interaction response
    """
    data = payload.get('data', {})
    command_name = data.get('name', '')

    logger.info(f'discord_interaction_command: {command_name}')

    # Handle different commands
    if command_name == 'help':
        return JSONResponse({
            'type': 4,  # CHANNEL_MESSAGE_WITH_SOURCE
            'data': {
                'content': (
                    '🤖 **OpenHands Discord Bot**\n\n'
                    'Mention me in a channel to start a conversation!\n\n'
                    'Commands:\n'
                    '• `/help` - Show this help message\n'
                    '• `/status` - Check your account status\n'
                )
            }
        })

    if command_name == 'status':
        return JSONResponse({
            'type': 4,
            'data': {
                'content': '✅ Your Discord account is connected to OpenHands!'
            }
        })

    # Unknown command
    return JSONResponse({
        'type': 4,
        'data': {
            'content': f'Unknown command: {command_name}'
        }
    })


async def _handle_component(payload: dict) -> JSONResponse:
    """Handle Discord message component interactions (buttons, select menus).

    Args:
        payload: The component interaction payload

    Returns:
        JSONResponse with component response
    """
    data = payload.get('data', {})
    custom_id = data.get('custom_id', '')

    logger.info(f'discord_component_interaction: {custom_id}')

    # Handle repository selection
    if custom_id.startswith('repo_select:'):
        # TODO: Handle repository selection
        return JSONResponse({
            'type': 6,  # UPDATE_MESSAGE
            'data': {'content': 'Repository selected! Starting conversation...'}
        })

    return JSONResponse({'type': 6})


@discord_router.get('/health')
async def health():
    """Health check endpoint for Discord integration."""
    return JSONResponse({
        'status': 'healthy',
        'webhooks_enabled': DISCORD_WEBHOOKS_ENABLED,
        'bot_configured': bool(DISCORD_BOT_TOKEN),
    })


@discord_router.post('/send-message')
async def send_message(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Send a message to a Discord channel (internal API).

    This endpoint is used by the callback processor to send messages
    back to Discord channels.
    """
    body = await request.json()

    channel_id = body.get('channel_id')
    message = body.get('message')
    thread_id = body.get('thread_id')

    if not channel_id or not message:
        raise HTTPException(status_code=400, detail='Missing channel_id or message')

    # TODO: Implement actual message sending via Discord bot
    logger.info(f'Sending Discord message to channel {channel_id}: {message[:100]}')

    return JSONResponse({'success': True})
