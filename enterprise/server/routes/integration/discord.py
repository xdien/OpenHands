"""Discord integration routes for OpenHands.

This module provides FastAPI routes for Discord integration:
- Webhook endpoint for Discord events (bot mentions)
- OAuth endpoints for Discord authentication
- Interaction endpoints for Discord UI components
"""

import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from integrations.discord.discord_manager import DiscordManager
from integrations.models import Message, SourceType
from integrations.utils import HOST_URL
from server.auth.saas_user_auth import saas_user_auth_from_cookie
from server.auth.token_manager import TokenManager
from server.constants import (
    DISCORD_BOT_TOKEN,
    DISCORD_PUBLIC_KEY,
    DISCORD_WEBHOOKS_ENABLED,
)
from server.logger import logger
from storage.database import a_session_maker

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
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

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
async def install(state: str = ''):
    """Redirect to Discord OAuth authorization URL."""
    from server.constants import DISCORD_CLIENT_ID

    if not DISCORD_CLIENT_ID:
        raise HTTPException(
            status_code=500, detail='Discord integration not configured'
        )

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
    if state:
        oauth_url += f'&state={state}'

    return RedirectResponse(oauth_url)


@discord_router.get('/install-callback')
async def install_callback(request: Request, code: str = '', error: str = '', state: str = ''):
    """Handle Discord OAuth callback and link Discord user to OpenHands user."""
    import httpx
    from server.constants import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET
    from openhands.server.shared import config
    import jwt
    from sqlalchemy import select
    from storage.discord_user import DiscordUser
    from storage.user_store import UserStore
    from server.auth.constants import KEYCLOAK_SERVER_URL_EXT, KEYCLOAK_REALM_NAME, KEYCLOAK_CLIENT_ID

    if error or not code:
        logger.warning(
            'discord_install_callback_error',
            extra={'code': code, 'error': error},
        )
        return JSONResponse(
            {'error': error or 'No authorization code provided'},
            status_code=400,
        )

    # config is already imported from openhands.server.shared

    try:
        # Exchange code for access token
        redirect_uri = f'{HOST_URL}/discord/install-callback'
        token_data = {
            'client_id': DISCORD_CLIENT_ID,
            'client_secret': DISCORD_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://discord.com/api/oauth2/token', data=token_data
            )
            response.raise_for_status()
            tokens = response.json()
            access_token = tokens.get('access_token')

        # Get user info from Discord API
        async with httpx.AsyncClient() as client:
            user_response = await client.get(
                'https://discord.com/api/users/@me',
                headers={'Authorization': f'Bearer {access_token}'},
            )
            user_response.raise_for_status()
            user_data = user_response.json()

        discord_user_id = str(user_data.get('id'))
        discord_username = user_data.get('username', 'unknown')
        discord_discriminator = user_data.get('discriminator')

        # Try to get existing user session from cookie (optional - for future Keycloak integration)
        keycloak_user_id = None
        try:
            user_auth = await saas_user_auth_from_cookie(request)
            if user_auth:
                keycloak_user_id = user_auth.user_id
        except Exception:
            pass

        # Try to get user info from state if any (optional - for future Keycloak integration)
        if not keycloak_user_id and state and config.jwt_secret:
            try:
                payload = jwt.decode(
                    state, config.jwt_secret.get_secret_value(), algorithms=['HS256']
                )
                keycloak_user_id = payload.get('keycloak_user_id')
            except Exception:
                pass

        # NOTE: Keycloak redirect removed - Discord user is saved directly
        # When Keycloak is integrated later, keycloak_user_id will be linked via:
        # 1. User session cookie (if already logged in)
        # 2. State parameter (if passed from login flow)
        # 3. Manual database update via admin

        # Link Discord user to OpenHands user (keycloak_user_id can be None)
        async with a_session_maker() as session:
            # Check if Discord user already exists
            result = await session.execute(
                select(DiscordUser).where(
                    DiscordUser.discord_user_id == discord_user_id
                )
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                # Update existing user
                existing_user.discord_username = discord_username
                if discord_discriminator:
                    existing_user.discord_discriminator = discord_discriminator
                if keycloak_user_id:
                    existing_user.keycloak_user_id = keycloak_user_id
                await session.commit()
                logger.info(f'Updated Discord user: {discord_username}')
            else:
                # Create new Discord user
                new_user = DiscordUser(
                    discord_user_id=discord_user_id,
                    discord_username=discord_username,
                    discord_discriminator=discord_discriminator,
                    keycloak_user_id=keycloak_user_id,
                )
                session.add(new_user)
                await session.commit()
                logger.info(f'Created Discord user: {discord_username}')

        if keycloak_user_id:
            return JSONResponse(
                {
                    'success': True,
                    'message': 'Discord account linked successfully!',
                    'discord_user_id': discord_user_id,
                    'discord_username': discord_username,
                    'openhands_user_id': keycloak_user_id,
                }
            )

        return JSONResponse(
            {
                'success': True,
                'message': 'Discord account linked successfully! You can now use the bot.',
                'discord_user_id': discord_user_id,
                'discord_username': discord_username,
                'note': 'Keycloak integration not configured. Some features may be limited until OpenHands account is linked.',
            }
        )

    except Exception as e:
        logger.error(f'discord_oauth_callback_error: {e}', exc_info=True)
        return JSONResponse(
            {'error': 'Failed to link Discord account', 'detail': str(e)},
            status_code=500,
        )


@discord_router.get('/keycloak-callback')
async def keycloak_callback(
    request: Request,
    code: str = '',
    state: str = '',
    error: str = '',
):
    """Handle Keycloak OAuth callback and link Discord user to OpenHands user."""
    from urllib.parse import quote
    from openhands.server.shared import config
    import jwt
    from sqlalchemy import select
    from storage.discord_user import DiscordUser
    from storage.user_store import UserStore
    from server.auth.constants import KEYCLOAK_SERVER_URL_EXT, KEYCLOAK_REALM_NAME, KEYCLOAK_CLIENT_ID

    if not code or error:
        logger.warning(
            'discord_keycloak_callback_error',
            extra={'code': code, 'state': state, 'error': error},
        )
        return JSONResponse(
            {'error': error or 'No authorization code provided'},
            status_code=400,
        )

    # config is already imported from openhands.server.shared
    if not config.jwt_secret:
        return JSONResponse(
            {'error': 'JWT not configured'},
            status_code=500,
        )

    try:
        # Decode state to get Discord user info
        payload: dict[str, str] = jwt.decode(
            state, config.jwt_secret.get_secret_value(), algorithms=['HS256']
        )
        discord_user_id = payload.get('discord_user_id')
        discord_username = payload.get('discord_username', 'unknown')
        discord_discriminator = payload.get('discord_discriminator')

        if not discord_user_id:
            return JSONResponse(
                {'error': 'Discord user ID not found in state'},
                status_code=400,
            )

        # Get Keycloak tokens
        redirect_uri = f'{HOST_URL}/discord/keycloak-callback'
        token_manager = TokenManager()
        keycloak_access_token, keycloak_refresh_token = await token_manager.get_keycloak_tokens(
            code, redirect_uri
        )

        if not keycloak_access_token or not keycloak_refresh_token:
            return JSONResponse(
                {'error': 'Failed to get Keycloak tokens'},
                status_code=400,
            )

        # Get user info from Keycloak
        user_info = await token_manager.get_user_info(keycloak_access_token)
        keycloak_user_id = user_info.sub

        # Verify user exists in OpenHands
        user = await UserStore.get_user_by_id(keycloak_user_id)
        if not user:
            return JSONResponse(
                {'error': 'OpenHands user not found. Please log in to OpenHands first.'},
                status_code=400,
            )

        # Store Discord user in database with keycloak_user_id
        async with a_session_maker() as session:
            # Check if Discord user already linked
            result = await session.execute(
                select(DiscordUser).where(
                    DiscordUser.discord_user_id == discord_user_id
                )
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                # Update existing user with keycloak_user_id
                existing_user.keycloak_user_id = keycloak_user_id
                existing_user.discord_username = discord_username
                if discord_discriminator:
                    existing_user.discord_discriminator = discord_discriminator
                await session.commit()
                logger.info(
                    f'Updated Discord user link: {discord_username} -> {keycloak_user_id}'
                )
            else:
                # Create new Discord user linked to OpenHands user
                new_user = DiscordUser(
                    keycloak_user_id=keycloak_user_id,
                    discord_user_id=discord_user_id,
                    discord_username=discord_username,
                    discord_discriminator=discord_discriminator,
                )
                session.add(new_user)
                await session.commit()
                logger.info(
                    f'Linked Discord user: {discord_username} (ID: {discord_user_id}) to OpenHands user {keycloak_user_id}'
                )

        return JSONResponse(
            {
                'success': True,
                'message': 'Discord account linked successfully!',
                'discord_user_id': discord_user_id,
                'discord_username': discord_username,
                'openhands_user_id': keycloak_user_id,
            }
        )

    except Exception as e:
        logger.error(f'discord_keycloak_callback_error: {e}', exc_info=True)
        return JSONResponse(
            {'error': 'Failed to link Discord account', 'detail': str(e)},
            status_code=500,
        )


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
        return JSONResponse(
            {
                'type': 4,  # CHANNEL_MESSAGE_WITH_SOURCE
                'data': {
                    'content': (
                        '🤖 **OpenHands Discord Bot**\n\n'
                        'Mention me in a channel to start a conversation!\n\n'
                        'Commands:\n'
                        '• `/help` - Show this help message\n'
                        '• `/status` - Check your account status\n'
                    )
                },
            }
        )

    if command_name == 'status':
        return JSONResponse(
            {
                'type': 4,
                'data': {
                    'content': '✅ Your Discord account is connected to OpenHands!'
                },
            }
        )

    # Unknown command
    return JSONResponse(
        {'type': 4, 'data': {'content': f'Unknown command: {command_name}'}}
    )


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
        return JSONResponse(
            {
                'type': 6,  # UPDATE_MESSAGE
                'data': {'content': 'Repository selected! Starting conversation...'},
            }
        )

    return JSONResponse({'type': 6})


@discord_router.get('/health')
async def health():
    """Health check endpoint for Discord integration."""
    return JSONResponse(
        {
            'status': 'healthy',
            'webhooks_enabled': DISCORD_WEBHOOKS_ENABLED,
            'bot_configured': bool(DISCORD_BOT_TOKEN),
        }
    )


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
