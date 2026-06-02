import os

from dotenv import load_dotenv

load_dotenv()

# Ensure SAAS configuration is used
if not os.getenv("OPENHANDS_CONFIG_CLS"):
    os.environ["OPENHANDS_CONFIG_CLS"] = "server.config.SaaSServerConfig"

# SaaS registers enterprise routes below, then mounts the frontend last. Avoid
# the base app's import-time SPA mount from shadowing those routes.
os.environ['SERVE_FRONTEND'] = 'false'

from fastapi import Request, status  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from server.auth.auth_error import ExpiredError, NoCredentialsError  # noqa: E402
from server.auth.constants import (  # noqa: E402
    BITBUCKET_APP_CLIENT_ID,
    BITBUCKET_DATA_CENTER_HOST,
    ENABLE_JIRA,
    ENABLE_JIRA_DC,
    GITHUB_APP_CLIENT_ID,
    GITLAB_APP_CLIENT_ID,
)
from server.constants import PERMITTED_CORS_ORIGINS  # noqa: E402
from server.logger import logger  # noqa: E402
from server.middleware import (  # noqa: E402
    ApiKeyAwareCORSMiddleware,
    SetAuthCookieMiddleware,
)
from server.rate_limit import setup_rate_limit_handler  # noqa: E402
from server.routes.analytics_events import analytics_events_router  # noqa: E402
from server.routes.api_keys import api_router as api_keys_router  # noqa: E402
from server.routes.auth import api_router, oauth_router  # noqa: E402
from server.routes.billing import billing_router  # noqa: E402
from server.routes.email import api_router as email_router  # noqa: E402
from server.routes.github_proxy import add_github_proxy_routes  # noqa: E402
from server.routes.integration.jira import jira_integration_router  # noqa: E402
from server.routes.integration.jira_dc import jira_dc_integration_router  # noqa: E402
from server.routes.integration.slack import slack_router  # noqa: E402
from server.routes.oauth_device import oauth_device_router  # noqa: E402
from server.routes.org_invitations import (  # noqa: E402
    accept_router as invitation_accept_router,
)
from server.routes.org_invitations import (  # noqa: E402
    invitation_router,
)
from server.routes.org_profiles import router as org_profiles_router  # noqa: E402
from server.routes.orgs import org_router  # noqa: E402
from server.routes.readiness import readiness_router  # noqa: E402
from server.routes.service import service_router  # noqa: E402
from server.routes.user_app_settings import user_app_settings_router  # noqa: E402
from server.routes.users_v1 import (  # noqa: E402
    override_users_me_endpoint,
)
from server.sharing.shared_conversation_router import (  # noqa: E402
    router as shared_conversation_router,
)
from server.sharing.shared_event_router import (  # noqa: E402
    router as shared_event_router,
)
from server.verified_models.verified_model_router import (  # noqa: E402
    api_router as verified_models_router,
)

from openhands.app_server.app import app as base_app  # noqa: E402
from openhands.app_server.middleware import (  # noqa: E402
    CacheControlMiddleware,
)
from openhands.app_server.static import SPAStaticFiles  # noqa: E402

# Import WebSocket proxy handler for sandbox routing
from openhands.app_server.websocket_proxy.websocket_proxy_router import (
    websocket_proxy_to_sandbox,
)  # noqa: E402

# Import HTTP proxy handler for sandbox services (VSCode, web apps, etc.)
from openhands.app_server.http_proxy.http_proxy_router import (
    http_proxy_to_sandbox,
    http_vscode_proxy_to_sandbox,
    http_worker1_proxy_to_sandbox,
    http_ws_proxy_to_sandbox,
)  # noqa: E402

from starlette.routing import Route, WebSocketRoute  # noqa: E402

# Debug: Print routes to verify WebSocket route is registered
import logging

_logger = logging.getLogger(__name__)

directory = os.getenv("FRONTEND_DIRECTORY", "./frontend/build")


@base_app.get("/saas")
def is_saas():
    return {"saas": True}


base_app.include_router(readiness_router)  # Add routes for readiness checks
base_app.include_router(api_router)  # Add additional route for github auth
base_app.include_router(oauth_router)  # Add additional route for oauth callback
base_app.include_router(oauth_device_router)  # Add OAuth 2.0 Device Flow routes
base_app.include_router(user_app_settings_router)  # Add routes for user app settings
base_app.include_router(
    billing_router
)  # Add routes for credit management and Stripe payment integration
base_app.include_router(shared_conversation_router)
base_app.include_router(shared_event_router)

# Add GitHub integration router only if GITHUB_APP_CLIENT_ID is set
if GITHUB_APP_CLIENT_ID:
    # Make sure that the callback processor is loaded here so we don't get an error when deserializing
    from integrations.github.github_v1_callback_processor import (  # noqa: E402
        GithubV1CallbackProcessor,
    )
    from server.routes.integration.github import github_integration_router  # noqa: E402

    # Bludgeon mypy into not deleting my import
    logger.debug(f"Loaded {GithubV1CallbackProcessor.__name__}")

    base_app.include_router(
        github_integration_router
    )  # Add additional route for integration webhook events

# Add GitLab integration router only if GITLAB_APP_CLIENT_ID is set
if GITLAB_APP_CLIENT_ID:
    # Make sure that the callback processor is loaded here so we don't get an error when deserializing
    from integrations.gitlab.gitlab_v1_callback_processor import (  # noqa: E402
        GitlabV1CallbackProcessor,
    )
    from server.routes.integration.gitlab import gitlab_integration_router  # noqa: E402

    # Bludgeon mypy into not deleting my import
    logger.debug(f'Loaded {GitlabV1CallbackProcessor.__name__}')

    base_app.include_router(gitlab_integration_router)

# Add Bitbucket Cloud integration router only if BITBUCKET_APP_CLIENT_ID is set
if BITBUCKET_APP_CLIENT_ID:
    from integrations.bitbucket.bitbucket_v1_callback_processor import (  # noqa: E402
        BitbucketV1CallbackProcessor,
    )
    from server.routes.integration.bitbucket import (  # noqa: E402
        bitbucket_integration_router,
    )

    logger.debug(f'Loaded {BitbucketV1CallbackProcessor.__name__}')

    base_app.include_router(bitbucket_integration_router)

base_app.include_router(api_keys_router)  # Add routes for API key management
base_app.include_router(service_router)  # Add routes for internal service API
base_app.include_router(org_router)  # Add routes for organization management
base_app.include_router(
    org_profiles_router, prefix='/api/organizations'
)  # Add routes for org LLM profiles
base_app.include_router(
    verified_models_router
)  # Add routes for verified models management

# Override the /api/v1/users/me endpoint to include organization info
# This replaces the OSS endpoint with a SAAS version that adds org_id, org_name, role, permissions
override_users_me_endpoint(base_app)

base_app.include_router(invitation_router)  # Add routes for org invitation management
base_app.include_router(invitation_accept_router)  # Add route for accepting invitations
add_github_proxy_routes(base_app)
base_app.include_router(slack_router)
if ENABLE_JIRA:
    base_app.include_router(jira_integration_router)
if ENABLE_JIRA_DC:
    base_app.include_router(jira_dc_integration_router)
if BITBUCKET_DATA_CENTER_HOST:
    from server.routes.bitbucket_dc_proxy import (
        router as bitbucket_dc_proxy_router,  # noqa: E402
    )

    base_app.include_router(bitbucket_dc_proxy_router)

    # Bitbucket Data Center resolver webhook (PR comment trigger).
    from integrations.bitbucket_data_center.bitbucket_dc_v1_callback_processor import (  # noqa: E402
        BitbucketDCV1CallbackProcessor,
    )
    from server.routes.integration.bitbucket_dc import (  # noqa: E402
        bitbucket_dc_integration_router,
    )

    logger.debug(f'Loaded {BitbucketDCV1CallbackProcessor.__name__}')

    base_app.include_router(bitbucket_dc_integration_router)
base_app.include_router(email_router)  # Add routes for email management
base_app.include_router(
    analytics_events_router
)  # Add routes for client-initiated analytics events


base_app.add_middleware(
    ApiKeyAwareCORSMiddleware,
    allow_origins=PERMITTED_CORS_ORIGINS,
)
base_app.add_middleware(CacheControlMiddleware)
base_app.middleware("http")(SetAuthCookieMiddleware())

# Add WebSocket proxy routes BEFORE SPAStaticFiles mount
# These routes handle WebSocket connections to sandbox agent servers
# They must be added BEFORE the '/' mount to ensure they are matched first
# Path pattern: /ws/{sandbox_port}/sockets/events/{conversation_id}
_logger.info("🔌 Registering WebSocket proxy routes...")
ws_route = WebSocketRoute(
    "/ws/{sandbox_port}/sockets/events/{conversation_id}",
    websocket_proxy_to_sandbox,
)
ws_planning_route = WebSocketRoute(
    "/ws/{sandbox_port}/sockets/events/{conversation_id}/planning/{planning_conversation_id}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_route)
base_app.routes.insert(0, ws_planning_route)
_logger.info(f"✅ WebSocket proxy routes registered: main + planning")

# Add VSCode-specific proxy route BEFORE generic proxy
# This route handles VSCode with special cookie-based auth
# Path pattern: /vscode/{sandbox_port}/...
_logger.info("🔌 Registering VSCode proxy route...")
vscode_route = Route(
    "/vscode/{sandbox_port}/{path:path}",
    http_vscode_proxy_to_sandbox,
)
base_app.routes.insert(0, vscode_route)
_logger.info(f"✅ VSCode proxy route registered: /vscode/{{sandbox_port}}/...")

# Add VSCode proxy route for root path (without trailing path)
vscode_root_route = Route(
    "/vscode/{sandbox_port}",
    http_vscode_proxy_to_sandbox,
)
base_app.routes.insert(0, vscode_root_route)
_logger.info(f"✅ VSCode root proxy route registered: /vscode/{{sandbox_port}}")

# Add WebSocket proxy route for /vscode/{sandbox_port}/{path:path}
# This handles VSCode WebSocket connections
# IMPORTANT: Must be registered BEFORE HTTP routes to take precedence
_logger.info("🔌 Registering WebSocket proxy routes for /vscode/{sandbox_port}/{path:path}...")
ws_vscode_route = WebSocketRoute(
    "/vscode/{sandbox_port}/{path:path}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_vscode_route)
_logger.info(f"✅ WebSocket proxy routes registered: /vscode/{{sandbox_port}}/{{path:path}}")

# Add WebSocket proxy route for /vscode/{sandbox_port}/ root path
ws_vscode_root_route = WebSocketRoute(
    "/vscode/{sandbox_port}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_vscode_root_route)
_logger.info(f"✅ WebSocket proxy routes registered: /vscode/{{sandbox_port}}/")

# Add Worker1-specific proxy route (for multi-container sandboxes like WORKER_1, WORKER_2)
_logger.info("🔌 Registering Worker1 proxy route...")
worker1_route = Route(
    "/worker1/{sandbox_port}/{path:path}",
    http_worker1_proxy_to_sandbox,
)
base_app.routes.insert(0, worker1_route)
_logger.info(f"✅ Worker1 proxy route registered: /worker1/{{sandbox_port}}/...")

# Add Worker1 proxy route for root path (without trailing path)
worker1_root_route = Route(
    "/worker1/{sandbox_port}",
    http_worker1_proxy_to_sandbox,
)
base_app.routes.insert(0, worker1_root_route)
_logger.info(f"✅ Worker1 root proxy route registered: /worker1/{{sandbox_port}}")

# Add WebSocket proxy route for /worker1/{sandbox_port}/{path:path}
_logger.info("🔌 Registering WebSocket proxy routes for /worker1/{sandbox_port}/{path:path}...")
ws_worker1_route = WebSocketRoute(
    "/worker1/{sandbox_port}/{path:path}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_worker1_route)
_logger.info(f"✅ WebSocket proxy routes registered: /worker1/{{sandbox_port}}/{{path:path}}")

# Add WebSocket proxy route for /worker1/{sandbox_port}/ root path
ws_worker1_root_route = WebSocketRoute(
    "/worker1/{sandbox_port}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_worker1_root_route)
_logger.info(f"✅ WebSocket proxy routes registered: /worker1/{{sandbox_port}}/")

# Add Worker2-specific proxy route
_logger.info("🔌 Registering Worker2 proxy route...")
worker2_route = Route(
    "/worker2/{sandbox_port}/{path:path}",
    http_worker1_proxy_to_sandbox,
)
base_app.routes.insert(0, worker2_route)
_logger.info(f"✅ Worker2 proxy route registered: /worker2/{{sandbox_port}}/...")

worker2_root_route = Route(
    "/worker2/{sandbox_port}",
    http_worker1_proxy_to_sandbox,
)
base_app.routes.insert(0, worker2_root_route)
_logger.info(f"✅ Worker2 root proxy route registered: /worker2/{{sandbox_port}}")

ws_worker2_route = WebSocketRoute(
    "/worker2/{sandbox_port}/{path:path}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_worker2_route)
_logger.info(f"✅ WebSocket proxy routes registered: /worker2/{{sandbox_port}}/{{path:path}}")

ws_worker2_root_route = WebSocketRoute(
    "/worker2/{sandbox_port}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_worker2_root_route)
_logger.info(f"✅ WebSocket proxy routes registered: /worker2/{{sandbox_port}}/")

# Add HTTP proxy routes BEFORE SPAStaticFiles mount
# These routes handle HTTP requests to sandbox services (VSCode, web apps, etc.)
# Path pattern: /proxy/{sandbox_port}/...
_logger.info("🔌 Registering HTTP proxy routes...")
http_route = Route(
    "/proxy/{sandbox_port}/{path:path}",
    http_proxy_to_sandbox,
)
base_app.routes.insert(0, http_route)
_logger.info(f"✅ HTTP proxy routes registered: /proxy/{{sandbox_port}}/...")

# Add HTTP proxy route for /ws/{sandbox_port}/api/* pattern
# This provides an alternative URL pattern for accessing sandbox APIs
_logger.info("🔌 Registering HTTP WS proxy routes for /ws/{sandbox_port}/api/*...")
http_ws_api_route = Route(
    "/ws/{sandbox_port}/api/{path:path}",
    http_ws_proxy_to_sandbox,
)
base_app.routes.insert(0, http_ws_api_route)
_logger.info(f"✅ HTTP WS proxy routes registered: /ws/{{sandbox_port}}/api/*")

# Add WebSocket proxy route for /ws/{sandbox_port}/{path:path}
# This handles VSCode browser WebSocket connections to sandbox
_logger.info("🔌 Registering WebSocket proxy routes for /ws/{sandbox_port}/{path:path}...")
ws_generic_route = WebSocketRoute(
    "/ws/{sandbox_port}/{path:path}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_generic_route)
_logger.info(f"✅ WebSocket proxy routes registered: /ws/{{sandbox_port}}/{{path:path}}")

# Add WebSocket proxy route for /ws/{sandbox_port}/ root path
_logger.info("🔌 Registering WebSocket proxy routes for /ws/{sandbox_port}/...")
ws_root_route = WebSocketRoute(
    "/ws/{sandbox_port}",
    websocket_proxy_to_sandbox,
)
base_app.routes.insert(0, ws_root_route)
_logger.info(f"✅ WebSocket proxy routes registered: /ws/{{sandbox_port}}/")

# Debug: List all routes
for i, route in enumerate(base_app.routes[:20]):
    _logger.info(f"Route {i}: {route.path if hasattr(route, 'path') else route}")

base_app.mount("/", SPAStaticFiles(directory=directory, html=True), name="dist")


setup_rate_limit_handler(base_app)


@base_app.exception_handler(NoCredentialsError)
async def no_credentials_exception_handler(request: Request, exc: NoCredentialsError):
    logger.info(exc.__class__.__name__)
    return JSONResponse(
        {"error": NoCredentialsError.__name__}, status.HTTP_401_UNAUTHORIZED
    )


@base_app.exception_handler(ExpiredError)
async def expired_exception_handler(request: Request, exc: ExpiredError):
    logger.info(exc.__class__.__name__)
    return JSONResponse({"error": ExpiredError.__name__}, status.HTTP_401_UNAUTHORIZED)


# Note: socketio is no longer used for communication. The base FastAPI app is used directly.
app = base_app
