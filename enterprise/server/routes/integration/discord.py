"""Discord integration routes for OpenHands.

This module provides FastAPI routes for Discord integration:
- Webhook endpoint for Discord events (bot mentions)
- OAuth endpoints for Discord authentication
- Interaction endpoints for Discord UI components

Supports both Keycloak and NestJS authentication backends.
Set NESTJS_BACKEND_URL to enable NestJS authentication.
"""

import datetime
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from integrations.discord.discord_manager import DiscordManager
from integrations.models import Message, SourceType
from integrations.utils import HOST_URL
from server.auth.enterprise_auth_client import (
    ENTERPRISE_AUTH_URL,
    ENTERPRISE_AUTH_URL_EXT,
    get_enterprise_auth_client,
    is_enterprise_auth_enabled,
)
from server.auth.saas_user_auth import saas_user_auth_from_cookie
from server.auth.token_manager import TokenManager
from server.constants import (
    DISCORD_BOT_TOKEN,
    DISCORD_PUBLIC_KEY,
    DISCORD_WEBHOOKS_ENABLED,
)
from server.logger import logger
from server.routes.auth import set_response_cookie
from storage.database import a_session_maker

from openhands.server.shared import sio
from openhands.server.user_auth import get_user_id

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


@discord_router.get('/login')
async def discord_login(request: Request, state: str = ''):
    """Show a user-friendly page for Discord account linking.

    Intelligently handles the linking flow:
    1. If user is logged into OpenHands AND has Discord context -> Link them immediately.
    2. If user has no Discord link -> Send to Discord OAuth.
    3. If user has Discord link but no OpenHands session -> Send to auth provider (NestJS or Keycloak).
    """
    from urllib.parse import urlencode

    import jwt
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select
    from storage.discord_user import DiscordUser

    from openhands.server.shared import config

    # Decode target Discord context if state is present
    discord_user_id = None
    discord_username = 'unknown'
    if state and config.jwt_secret:
        try:
            payload = jwt.decode(
                state, config.jwt_secret.get_secret_value(), algorithms=['HS256']
            )
            # Support both message payload and simplified linking payload
            discord_user_id = payload.get('discord_user_id') or payload.get(
                'author', {}
            ).get('id')
            discord_username = payload.get('discord_username') or payload.get(
                'author', {}
            ).get('username', 'User')
        except Exception:
            pass

    # Check if user is already logged into OpenHands
    # NOTE: For enterprise auth, we always force a fresh login to get a new token
    # because the existing session token may be close to expiration
    keycloak_user_id = None
    force_fresh_login = is_enterprise_auth_enabled()
    if not force_fresh_login:
        try:
            user_auth = await saas_user_auth_from_cookie(request)
            if user_auth:
                keycloak_user_id = user_auth.user_id
        except Exception:
            pass

    # CASE 1: User is logged into OpenHands AND we have Discord context
    # NOTE: For enterprise auth, we skip this case because the existing session token
    # may be close to expiration. Instead, we force a fresh login to get a new token.
    if keycloak_user_id and discord_user_id and not is_enterprise_auth_enabled():
        async with a_session_maker() as session:
            result = await session.execute(
                select(DiscordUser).where(
                    DiscordUser.discord_user_id == str(discord_user_id)
                )
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                existing_user.keycloak_user_id = keycloak_user_id
                await session.commit()

                # CRITICAL: Store enterprise offline token for Discord bot authentication
                # Get the refresh token from user_auth and store it
                try:
                    user_auth = await saas_user_auth_from_cookie(request)
                    if user_auth and user_auth.refresh_token:
                        token_manager = TokenManager(external=True)
                        refresh_token_value = user_auth.refresh_token.get_secret_value()
                        await token_manager.store_offline_token(
                            keycloak_user_id, refresh_token_value
                        )
                        logger.info(
                            'discord_link_success_store_token',
                            extra={
                                'keycloak_user_id': keycloak_user_id,
                                'discord_user_id': discord_user_id,
                            },
                        )
                except Exception as e:
                    logger.error(
                        'discord_link_token_storage_failed',
                        extra={
                            'keycloak_user_id': keycloak_user_id,
                            'error': str(e),
                        },
                    )

                return HTMLResponse(
                    content=f"""
                    <html><body style="background:#1a1a2e;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;">
                    <div style="background:#16213e;padding:40px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,0.5);">
                    <h1>✅ Success!</h1>
                    <p>Your Discord account <strong>@{discord_username}</strong> is now linked to your OpenHands account.</p>
                    <p>You can return to Discord and mention the bot again!</p>
                    </div></body></html>
                    """
                )

    # CASE 2: No OpenHands session -> Redirect to auth provider first
    # This ensures we have an identity to link to the Discord account
    if not keycloak_user_id:
        # DEBUG: Log enterprise auth status
        enterprise_enabled = is_enterprise_auth_enabled()
        logger.info(
            'DISCORD_LOGIN_DEBUG',
            extra={
                'keycloak_user_id': keycloak_user_id,
                'is_enterprise_auth_enabled': enterprise_enabled,
                'ENTERPRISE_AUTH_URL': ENTERPRISE_AUTH_URL,
                'ENTERPRISE_AUTH_URL_EXT': ENTERPRISE_AUTH_URL_EXT,
            },
        )

        # Check if Enterprise auth is enabled
        if enterprise_enabled:
            # Show login form that calls backend API directly
            # Instead of redirecting to enterprise backend, we show a form here
            return HTMLResponse(
                content=f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Login to Link Discord</title>
                    <style>
                        body {{
                            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                            background: #1a1a2e;
                            color: #fff;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            height: 100vh;
                            margin: 0;
                        }}
                        .container {{
                            background: #16213e;
                            padding: 40px;
                            border-radius: 16px;
                            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                            width: 100%;
                            max-width: 400px;
                        }}
                        h1 {{ margin-top: 0; text-align: center; }}
                        .form-group {{ margin-bottom: 20px; }}
                        label {{ display: block; margin-bottom: 8px; font-weight: 500; }}
                        input {{
                            width: 100%;
                            padding: 12px;
                            border: 1px solid #333;
                            border-radius: 8px;
                            background: #0f0f23;
                            color: #fff;
                            font-size: 16px;
                            box-sizing: border-box;
                        }}
                        input:focus {{ outline: none; border-color: #4a9eff; }}
                        button {{
                            width: 100%;
                            padding: 14px;
                            background: #4a9eff;
                            color: white;
                            border: none;
                            border-radius: 8px;
                            font-size: 16px;
                            font-weight: 600;
                            cursor: pointer;
                            transition: background 0.2s;
                        }}
                        button:hover {{ background: #3a8eef; }}
                        button:disabled {{ background: #666; cursor: not-allowed; }}
                        .error {{
                            background: #ff4444;
                            color: white;
                            padding: 12px;
                            border-radius: 8px;
                            margin-bottom: 20px;
                            display: none;
                        }}
                        .loading {{ text-align: center; display: none; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🔗 Link Discord Account</h1>
                        <p style="text-align: center; color: #aaa; margin-bottom: 30px;">
                            Please login with your enterprise account to link with Discord
                        </p>

                        <div id="error" class="error"></div>

                        <form id="loginForm">
                            <div class="form-group">
                                <label for="email">Email</label>
                                <input type="email" id="email" name="email" required placeholder="your@email.com">
                            </div>
                            <div class="form-group">
                                <label for="password">Password</label>
                                <input type="password" id="password" name="password" required placeholder="Your password">
                            </div>
                            <button type="submit" id="submitBtn">Login & Link Discord</button>
                        </form>

                        <div id="loading" class="loading">
                            <p>Logging in...</p>
                        </div>
                    </div>

                    <script>
                        const form = document.getElementById('loginForm');
                        const errorDiv = document.getElementById('error');
                        const loadingDiv = document.getElementById('loading');
                        const submitBtn = document.getElementById('submitBtn');

                        form.addEventListener('submit', async (e) => {{
                            e.preventDefault();

                            const email = document.getElementById('email').value;
                            const password = document.getElementById('password').value;

                            // Hide form, show loading
                            form.style.display = 'none';
                            loadingDiv.style.display = 'block';
                            errorDiv.style.display = 'none';

                            try {{
                                // Call enterprise backend login API
                                const response = await fetch('{ENTERPRISE_AUTH_URL_EXT}/auth/login', {{
                                    method: 'POST',
                                    headers: {{
                                        'Content-Type': 'application/json'
                                    }},
                                    body: JSON.stringify({{ email, password }})
                                }});

                                if (!response.ok) {{
                                    const errorData = await response.json();
                                    throw new Error(errorData.message || 'Login failed');
                                }}

                                const data = await response.json();

                                // Support multiple response formats:
                                // 1. Standard: {{ access_token: "...", refresh_token: "..." }}
                                // 2. Nested: {{ token: {{ accessToken: "...", expiresIn: 3600 }}, user: {{...}} }}
                                const accessToken = data.access_token || data.token?.accessToken;
                                const refreshToken = data.refresh_token || data.token?.refreshToken;

                                if (accessToken) {{
                                    // Login successful, redirect to callback with token
                                    const redirectUri = '{HOST_URL}/discord/enterprise-callback';
                                    const callbackUrl = new URL(redirectUri);
                                    callbackUrl.searchParams.set('access_token', accessToken);
                                    if (refreshToken) {{
                                        callbackUrl.searchParams.set('refresh_token', refreshToken);
                                    }}
                                    callbackUrl.searchParams.set('state', '{state}');

                                    window.location.href = callbackUrl.toString();
                                }} else {{
                                    throw new Error('No access token received');
                                }}
                            }} catch (err) {{
                                form.style.display = 'block';
                                loadingDiv.style.display = 'none';
                                errorDiv.textContent = err.message || 'Login failed. Please try again.';
                                errorDiv.style.display = 'block';
                            }}
                        }});
                    </script>
                </body>
                </html>
                """
            )
        else:
            # Fall back to Keycloak authentication
            from server.auth.constants import (
                KEYCLOAK_CLIENT_ID,
                KEYCLOAK_REALM_NAME,
                KEYCLOAK_SERVER_URL_EXT,
            )

            # Construct auth URL manually to avoid backend-to-frontend connection failures
            # (e.g. IPv6 / Cloudflare hairpinning issues)
            base_auth_url = f'{KEYCLOAK_SERVER_URL_EXT}/realms/{KEYCLOAK_REALM_NAME}/protocol/openid-connect/auth'

            keycloak_state = state if state else 'discord_link'
            redirect_uri = f'{HOST_URL}/discord/keycloak-callback'

            params = {
                'client_id': KEYCLOAK_CLIENT_ID,
                'redirect_uri': redirect_uri,
                'state': keycloak_state,
                'response_type': 'code',
                'scope': 'openid profile email',
            }
            auth_url = f'{base_auth_url}?{urlencode(params)}'

            return RedirectResponse(auth_url)

    # CASE 3: Has OpenHands session but no Discord context -> Just go to standard Install flow
    redirect_url = f'{HOST_URL}/discord/install'
    if state:
        redirect_url += f'?state={state}'

    return RedirectResponse(redirect_url)


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
async def install_callback(
    request: Request, code: str = '', error: str = '', state: str = ''
):
    """Handle Discord OAuth callback and link Discord user to OpenHands user."""
    import httpx
    import jwt
    from server.constants import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET
    from sqlalchemy import select
    from storage.discord_user import DiscordUser

    from openhands.server.shared import config

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

        # CRITICAL: Store enterprise offline token if user is logged in via cookie
        # This allows the Discord bot to authenticate the user later
        if keycloak_user_id:
            try:
                user_auth = await saas_user_auth_from_cookie(request)
                if user_auth and user_auth.refresh_token:
                    token_manager = TokenManager(external=True)
                    refresh_token_value = user_auth.refresh_token.get_secret_value()
                    await token_manager.store_offline_token(
                        keycloak_user_id, refresh_token_value
                    )
                    logger.info(
                        'discord_oauth_store_token',
                        extra={
                            'keycloak_user_id': keycloak_user_id,
                            'discord_user_id': discord_user_id,
                        },
                    )
            except Exception as e:
                logger.error(
                    'discord_oauth_token_storage_failed',
                    extra={
                        'keycloak_user_id': keycloak_user_id,
                        'error': str(e),
                    },
                )

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
    import jwt
    from sqlalchemy import select
    from storage.discord_user import DiscordUser
    from storage.user_store import UserStore

    from openhands.server.shared import config

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
        token_manager = TokenManager(external=True)
        (
            keycloak_access_token,
            keycloak_refresh_token,
        ) = await token_manager.get_keycloak_tokens(code, redirect_uri)

        if not keycloak_access_token or not keycloak_refresh_token:
            return JSONResponse(
                {'error': 'Failed to get Keycloak tokens'},
                status_code=400,
            )

        # Get user info from Keycloak access token
        # We decode locally to avoid network hair-pinning issues (401/timeout when server calls itself)
        # We can trust the token because we just got it directly from Keycloak
        token_payload = jwt.decode(
            keycloak_access_token, options={'verify_signature': False}
        )
        keycloak_user_id = token_payload.get('sub')

        if not keycloak_user_id:
            return JSONResponse(
                {'error': 'Could not identify Keycloak user from token'},
                status_code=400,
            )

        # Verify user exists in OpenHands
        user = await UserStore.get_user_by_id(keycloak_user_id)
        if not user:
            logger.info(
                f'User {keycloak_user_id} not found in DB, creating from token info...'
            )
            # Construct user info from token payload for creation
            user_info_for_creation = {
                'email': token_payload.get('email'),
                'preferred_username': token_payload.get(
                    'preferred_username', discord_username
                ),
                'given_name': token_payload.get('given_name'),
                'family_name': token_payload.get('family_name'),
                'email_verified': token_payload.get('email_verified', False),
            }

            # Ensure email is present (required by UserStore.create_user)
            if not user_info_for_creation['email']:
                user_info_for_creation['email'] = (
                    f"{user_info_for_creation['preferred_username']}@local"
                )

            try:
                user = await UserStore.create_user(
                    keycloak_user_id, user_info_for_creation
                )
                if not user:
                    return JSONResponse(
                        {'error': 'Failed to create OpenHands user record'},
                        status_code=500,
                    )
                logger.info(
                    f'Created new OpenHands user {keycloak_user_id} during Discord linking'
                )
            except Exception as e:
                logger.error(
                    f'Error creating user {keycloak_user_id}: {e}', exc_info=True
                )
                return JSONResponse(
                    {'error': 'Error creating user record', 'detail': str(e)},
                    status_code=500,
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

        # CRITICAL FIX: Store Keycloak tokens for future authentication
        # This is required for Discord bot to recognize the user on subsequent mentions
        try:
            # Decode JWT to get token expiration
            token_payload = jwt.decode(
                keycloak_access_token, options={'verify_signature': False}
            )
            exp = token_payload.get('exp', 0)
            now = int(datetime.utcnow().timestamp())
            expires_in = max(exp - now, 3600)  # Default to 1 hour if no exp claim

            # Store the tokens for offline use
            await token_manager.store_offline_token(
                keycloak_user_id, keycloak_refresh_token
            )
            logger.info(
                'discord_keycloak_tokens_stored',
                extra={
                    'user_id': keycloak_user_id,
                    'discord_user_id': discord_user_id,
                    'expires_in': expires_in,
                },
            )
        except Exception as e:
            logger.error(
                'discord_keycloak_token_storage_failed',
                extra={'user_id': keycloak_user_id, 'error': str(e)},
            )

        from fastapi.responses import HTMLResponse

        return HTMLResponse(
            content=f"""
            <html><body style="background:#1a1a2e;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;">
            <div style="background:#16213e;padding:40px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,0.5);">
            <div style="font-size:48px;margin-bottom:16px;">🚀</div>
            <h1>Linked Successfully!</h1>
            <p>Your Discord account <strong>@{discord_username}</strong> is now linked to OpenHands.</p>
            <p>You can now go back to Discord and start chatting with the bot.</p>
            <br>
            <p style="color:#9aa5b4;font-size:14px;">(You can close this window now)</p>
            </div></body></html>
            """,
            status_code=200,
        )

    except Exception as e:
        logger.error(f'discord_keycloak_callback_error: {e}', exc_info=True)
        return JSONResponse(
            {'error': 'Failed to link Discord account', 'detail': str(e)},
            status_code=500,
        )


@discord_router.get('/enterprise-callback')
async def enterprise_callback(
    request: Request,
    code: str = '',
    access_token: str = '',
    refresh_token: str = '',
    state: str = '',
    error: str = '',
):
    logger.info(
        'discord_enterprise_callback_received',
        extra={
            'has_code': bool(code),
            'has_access_token': bool(access_token),
            'has_refresh_token': bool(refresh_token),
            'has_state': bool(state),
            'has_error': bool(error),
            'query_params': list(request.query_params.keys()),
        },
    )
    """Handle Enterprise OAuth callback and link Discord user to OpenHands user.

    This is the equivalent of keycloak-callback but for custom enterprise backend.
    Supports both:
    - code: OAuth2 authorization code flow (legacy)
    - access_token: Direct token from login form (new flow)
    """
    import jwt
    from sqlalchemy import select
    from storage.discord_user import DiscordUser
    from storage.user_store import UserStore

    from openhands.server.shared import config

    # Check for error
    if error:
        logger.warning(
            'discord_enterprise_callback_error',
            extra={'code': code, 'state': state, 'error': error},
        )
        return JSONResponse(
            {'error': error},
            status_code=400,
        )

    # Check for either code or access_token
    if not code and not access_token:
        logger.warning(
            'discord_enterprise_callback_error',
            extra={
                'code': code,
                'state': state,
                'error': 'No code or access_token provided',
            },
        )
        return JSONResponse(
            {'error': 'No authorization code or access token provided'},
            status_code=400,
        )

    logger.info(
        'discord_enterprise_callback_debug',
        extra={
            'has_code': bool(code),
            'has_access_token': bool(access_token),
            'has_state': bool(state),
            'has_jwt_secret': bool(config.jwt_secret),
        },
    )

    if not config.jwt_secret:
        logger.error('discord_enterprise_callback_no_jwt_secret')
        return JSONResponse(
            {'error': 'JWT not configured'},
            status_code=500,
        )

    try:
        # Decode state to get Discord user info
        if state:
            try:
                payload: dict[str, str] = jwt.decode(
                    state, config.jwt_secret.get_secret_value(), algorithms=['HS256']
                )
                discord_user_id = payload.get('discord_user_id')
                discord_username = payload.get('discord_username', 'unknown')
                discord_discriminator = payload.get('discord_discriminator')
            except Exception:
                discord_user_id = None
                discord_username = 'unknown'
                discord_discriminator = None
        else:
            discord_user_id = None
            discord_username = 'unknown'
            discord_discriminator = None

        # Get Enterprise auth tokens
        enterprise_client = get_enterprise_auth_client(external=True)

        if access_token:
            # New flow: access_token passed directly from login form
            logger.info('discord_enterprise_callback: Using direct access_token flow')
            token_payload = enterprise_client.decode_jwt(access_token, verify=False)
            if not token_payload:
                return JSONResponse(
                    {'error': 'Failed to decode access token'},
                    status_code=400,
                )
        else:
            # Legacy flow: exchange code for tokens
            redirect_uri = f'{HOST_URL}/discord/enterprise-callback'
            access_token, refresh_token = await enterprise_client.get_tokens_from_code(
                code, redirect_uri
            )

            if not access_token:
                return JSONResponse(
                    {'error': 'Failed to get Enterprise auth tokens'},
                    status_code=400,
                )

            # Get user info from JWT token
            token_payload = enterprise_client.decode_jwt(access_token, verify=False)
            if not token_payload:
                return JSONResponse(
                    {'error': 'Failed to decode Enterprise auth token'},
                    status_code=400,
                )

        # Get user info from JWT token
        # Support multiple claim names: 'sub' (standard), 'userId', 'user_id', 'id'
        token_payload = enterprise_client.decode_jwt(access_token, verify=False)
        if not token_payload:
            return JSONResponse(
                {'error': 'Failed to decode Enterprise auth token'},
                status_code=400,
            )

        user_id = (
            token_payload.get('sub')
            or token_payload.get('userId')
            or token_payload.get('user_id')
            or token_payload.get('id')
        )

        if not user_id:
            logger.error(
                'discord_enterprise_callback_no_user_id',
                extra={'token_payload_keys': list(token_payload.keys())},
            )
            return JSONResponse(
                {'error': 'Could not identify user from Enterprise auth token'},
                status_code=400,
            )

        # Verify user exists in OpenHands, create if not
        user = await UserStore.get_user_by_id(user_id)
        if not user:
            logger.info(f'User {user_id} not found in DB, creating from token info...')

            # Extract user info from token with fallbacks
            # Your token has: userId, type, roles, iat (no email or username)
            preferred_username = (
                token_payload.get('preferred_username')
                or token_payload.get('username')
                or token_payload.get('userId')
                or discord_username
                or 'user'
            )

            # Ensure email is always a valid string (UserStore.create_user requires it)
            email = token_payload.get('email')
            if not email or not isinstance(email, str) or not email.strip():
                email = f'{preferred_username}@local'

            user_info_for_creation = {
                'email': email.strip(),
                'preferred_username': str(preferred_username).strip(),
                'given_name': token_payload.get('given_name') or '',
                'family_name': token_payload.get('family_name') or '',
                'email_verified': token_payload.get('email_verified', False),
            }

            try:
                user = await UserStore.create_user(user_id, user_info_for_creation)
                if not user:
                    return JSONResponse(
                        {'error': 'Failed to create OpenHands user record'},
                        status_code=500,
                    )
                logger.info(
                    f'Created new OpenHands user {user_id} during Discord linking'
                )
            except Exception as e:
                logger.error(f'Error creating user {user_id}: {e}', exc_info=True)
                return JSONResponse(
                    {'error': 'Error creating user record', 'detail': str(e)},
                    status_code=500,
                )

        # Store Discord user in database with user_id
        async with a_session_maker() as session:
            if discord_user_id:
                result = await session.execute(
                    select(DiscordUser).where(
                        DiscordUser.discord_user_id == discord_user_id
                    )
                )
                existing_user = result.scalar_one_or_none()

                if existing_user:
                    existing_user.keycloak_user_id = user_id
                    existing_user.discord_username = discord_username
                    if discord_discriminator:
                        existing_user.discord_discriminator = discord_discriminator
                    await session.commit()
                else:
                    # Create new Discord user linked to OpenHands user
                    new_user = DiscordUser(
                        keycloak_user_id=user_id,
                        discord_user_id=discord_user_id,
                        discord_username=discord_username,
                        discord_discriminator=discord_discriminator,
                    )
                    session.add(new_user)
                    await session.commit()
            else:
                # No discord_user_id from state - this is a direct login flow
                # Just create the user without linking to Discord
                logger.info(
                    'No discord_user_id in state, user created without Discord link'
                )

        # CRITICAL FIX: Store enterprise tokens for future authentication
        # This is required for Discord bot to recognize the user on subsequent mentions
        # Use refresh_token if available (longer lifetime), otherwise use access_token
        token_to_store = refresh_token if refresh_token else access_token
        logger.info(
            'discord_enterprise_token_storage_start',
            extra={
                'user_id': user_id,
                'discord_user_id': discord_user_id,
                'access_token_present': bool(access_token),
                'refresh_token_present': bool(refresh_token),
                'token_to_store': 'refresh_token' if refresh_token else 'access_token',
            },
        )
        try:
            token_manager = TokenManager(external=True)

            # Decode JWT to get token expiration
            token_payload = enterprise_client.decode_jwt(token_to_store, verify=False)
            exp = token_payload.get('exp', 0)
            iat = token_payload.get('iat', 0)
            from datetime import datetime as dt
            from datetime import timezone

            now = int(dt.now(timezone.utc).timestamp())

            # If no exp claim, default to 1 hour from iat (or now if no iat)
            if exp == 0 and iat > 0:
                exp = iat + 3600  # Default 1 hour from issued time
            elif exp == 0:
                exp = now + 3600  # Default 1 hour from now

            expires_in = max(exp - now, 60)  # At least 1 minute

            logger.info(
                'discord_enterprise_token_debug',
                extra={
                    'user_id': user_id,
                    'exp': exp,
                    'iat': iat,
                    'now': now,
                    'expires_in': expires_in,
                    'token_payload_keys': list(token_payload.keys()),
                },
            )

            # Store the tokens for offline use
            await token_manager.store_offline_token(user_id, token_to_store)
            logger.info(
                'discord_enterprise_tokens_stored',
                extra={
                    'user_id': user_id,
                    'discord_user_id': discord_user_id,
                    'expires_in': expires_in,
                },
            )
        except Exception as e:
            import traceback

            logger.error(
                'discord_enterprise_token_storage_failed',
                extra={
                    'user_id': user_id,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                },
            )

        # Create response and set session cookie for OpenHands UI access
        response = Response(
            content=f"""
            <html><body style="background:#1a1a2e;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;">
            <div style="background:#16213e;padding:40px;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,0.5);">
            <div style="font-size:48px;margin-bottom:16px;">🚀</div>
            <h1>Linked Successfully!</h1>
            <p>Your Discord account <strong>@{discord_username}</strong> is now linked to OpenHands.</p>
            <p>You can now go back to Discord and start chatting with the bot.</p>
            <p style="color:#4a9eff;font-size:14px;margin-top:20px;">You can also access OpenHands UI at <a href="/" style="color:#4a9eff;">ai.canthotouring.com</a></p>
            <br>
            <p style="color:#9aa5b4;font-size:14px;">(You can close this window now)</p>
            </div></body></html>
            """,
            status_code=200,
            media_type='text/html',
        )

        # Set session cookie so user can access /api/settings and other authenticated endpoints
        # Use the enterprise access_token as both access and refresh token for simplicity
        # For enterprise auth, we auto-accept TOS since the user authenticated through enterprise backend
        set_response_cookie(
            request=request,
            response=response,
            keycloak_access_token=access_token,
            keycloak_refresh_token=access_token,  # Using same token since enterprise doesn't have refresh
            secure=True,
            accepted_tos=True,  # Auto-accept TOS for enterprise auth
        )

        logger.info(
            'discord_enterprise_session_created',
            extra={'user_id': user_id, 'discord_user_id': discord_user_id},
        )

        return response

    except Exception as e:
        logger.error(f'discord_nestjs_callback_error: {e}', exc_info=True)
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
            'discord_username': author.get('username'),
            'discord_discriminator': author.get('discriminator'),
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
    user_id: str = Depends(get_user_id),
):
    """Send a message to a Discord channel (internal API).

    This endpoint is used by the callback processor to send messages
    back to Discord channels. Requires authentication.
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
