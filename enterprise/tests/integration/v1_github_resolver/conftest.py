"""
Fixtures for V1 GitHub Resolver integration tests.

These tests run actual conversations with the ProcessSandboxService,
using TestLLM to replay pre-recorded trajectories.
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set environment before importing app modules
os.environ.setdefault('RUNTIME', 'process')
os.environ.setdefault('ENABLE_V1_GITHUB_RESOLVER', 'true')
os.environ.setdefault('GITHUB_APP_CLIENT_ID', 'test-app-id')
os.environ.setdefault('GITHUB_APP_CLIENT_SECRET', 'test-app-secret')
os.environ.setdefault('GITHUB_APP_PRIVATE_KEY', 'test-private-key')
os.environ.setdefault('GITHUB_APP_WEBHOOK_SECRET', 'test-webhook-secret')
os.environ.setdefault('GITHUB_WEBHOOKS_ENABLED', '1')
os.environ.setdefault('HOST', 'localhost')
os.environ.setdefault('KEYCLOAK_URL', 'http://localhost:8080')
os.environ.setdefault('KEYCLOAK_REALM', 'test-realm')
os.environ.setdefault('KEYCLOAK_CLIENT_ID', 'test-client')
os.environ.setdefault('KEYCLOAK_CLIENT_SECRET', 'test-secret')

# Set the templates directory to the absolute path
_repo_root = Path(__file__).parent.parent.parent.parent.parent
_templates_dir = _repo_root / 'openhands' / 'integrations' / 'templates' / 'resolver'
os.environ.setdefault('OPENHANDS_RESOLVER_TEMPLATES_DIR', str(_templates_dir) + '/')

# Import storage models for database setup
# Note: Import ALL models to ensure tables are created
# NOTE: Imports must come after environment setup, hence noqa: E402
from server.constants import ORG_SETTINGS_VERSION  # noqa: E402
from storage.auth_tokens import AuthTokens  # noqa: E402
from storage.base import Base  # noqa: E402
from storage.billing_session import BillingSession  # noqa: E402, F401
from storage.conversation_work import ConversationWork  # noqa: E402, F401
from storage.device_code import DeviceCode  # noqa: E402, F401
from storage.feedback import Feedback  # noqa: E402, F401
from storage.github_app_installation import GithubAppInstallation  # noqa: E402
from storage.org import Org  # noqa: E402
from storage.org_invitation import OrgInvitation  # noqa: E402, F401
from storage.org_member import OrgMember  # noqa: E402
from storage.role import Role  # noqa: E402
from storage.stored_conversation_metadata import (  # noqa: E402
    StoredConversationMetadata,  # noqa: F401
)
from storage.stored_conversation_metadata_saas import (  # noqa: E402
    StoredConversationMetadataSaas,  # noqa: F401
)
from storage.stored_offline_token import StoredOfflineToken  # noqa: E402
from storage.stripe_customer import StripeCustomer  # noqa: E402, F401
from storage.user import User  # noqa: E402

# Test constants
TEST_USER_UUID = UUID('11111111-1111-1111-1111-111111111111')
TEST_ORG_UUID = UUID('22222222-2222-2222-2222-222222222222')
TEST_KEYCLOAK_USER_ID = 'test-keycloak-user-id'
TEST_GITHUB_USER_ID = 12345
TEST_GITHUB_USERNAME = 'test-github-user'
TEST_WEBHOOK_SECRET = 'test-webhook-secret'


@pytest.fixture(scope='session')
def test_env():
    """Environment variables for testing."""
    return {
        'RUNTIME': 'process',
        'ENABLE_V1_GITHUB_RESOLVER': 'true',
        'GITHUB_APP_CLIENT_ID': 'test-app-id',
        'GITHUB_APP_WEBHOOK_SECRET': TEST_WEBHOOK_SECRET,
        'GITHUB_WEBHOOKS_ENABLED': '1',
    }


@pytest.fixture
def engine():
    """Create an in-memory SQLite database engine for enterprise tables."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session_maker(engine):
    """Create a session maker bound to the test engine."""
    return sessionmaker(bind=engine)


TEST_INSTALLATION_ID = 123456


@pytest.fixture
def seeded_db(session_maker):
    """Seed the database with test user data."""
    now = datetime.now(tz=None)  # Use naive datetime for SQLite compatibility

    with session_maker() as session:
        # Create role
        session.add(Role(id=1, name='admin', rank=1))

        # Create org with V1 enabled
        session.add(
            Org(
                id=TEST_ORG_UUID,
                name='test-org',
                org_version=ORG_SETTINGS_VERSION,
                enable_default_condenser=True,
                enable_proactive_conversation_starters=False,
                v1_enabled=True,
            )
        )

        # Create user
        session.add(
            User(
                id=TEST_USER_UUID,
                current_org_id=TEST_ORG_UUID,
                user_consents_to_analytics=True,
            )
        )

        # Create org member with LLM API key
        session.add(
            OrgMember(
                org_id=TEST_ORG_UUID,
                user_id=TEST_USER_UUID,
                role_id=1,
                llm_api_key='test-llm-api-key',
                status='active',
            )
        )

        # Create offline token for Keycloak user
        session.add(
            StoredOfflineToken(
                user_id=TEST_KEYCLOAK_USER_ID,
                offline_token='test-offline-token',
                created_at=now,
                updated_at=now,
            )
        )

        # Create auth tokens linking Keycloak user to GitHub
        future_time = int((now + timedelta(hours=1)).timestamp())
        session.add(
            AuthTokens(
                keycloak_user_id=TEST_KEYCLOAK_USER_ID,
                identity_provider='github',
                access_token='test-github-access-token',
                refresh_token='test-github-refresh-token',
                access_token_expires_at=future_time,
                refresh_token_expires_at=future_time + 86400,
            )
        )

        # Create GitHub app installation
        session.add(
            GithubAppInstallation(
                installation_id=str(TEST_INSTALLATION_ID),
                encrypted_token='test-encrypted-token',
                created_at=now,
                updated_at=now,
            )
        )

        session.commit()

    return session_maker


@pytest.fixture
def patched_session_maker(seeded_db):
    """Patch all imports of session_maker to use our test database.

    This is necessary because the enterprise code imports session_maker
    at module level from storage.database.
    """
    patches = [
        patch('storage.database.session_maker', seeded_db),
        patch('integrations.github.github_view.session_maker', seeded_db),
        patch('integrations.github.github_solvability.session_maker', seeded_db),
        patch('server.auth.token_manager.session_maker', seeded_db),
        patch('server.auth.saas_user_auth.session_maker', seeded_db),
        patch('server.auth.domain_blocker.session_maker', seeded_db),
    ]

    for p in patches:
        p.start()

    yield seeded_db

    for p in patches:
        p.stop()


@pytest.fixture
def mock_keycloak():
    """Mock Keycloak admin API to return our test user."""

    async def mock_get_users(query: dict) -> list[dict]:
        """Mock user lookup by GitHub ID."""
        q = query.get('q', '')
        if f'github_id:{TEST_GITHUB_USER_ID}' in q:
            return [{'id': TEST_KEYCLOAK_USER_ID, 'username': TEST_GITHUB_USERNAME}]
        return []

    mock_admin = MagicMock()
    mock_admin.a_get_users = AsyncMock(side_effect=mock_get_users)

    with patch('server.auth.token_manager.get_keycloak_admin', return_value=mock_admin):
        yield mock_admin


@pytest.fixture
def mock_github_api():
    """Mock PyGithub API to capture posted comments."""
    captured_comments = []
    captured_reactions = []

    mock_issue = MagicMock()
    mock_issue.create_comment = MagicMock(
        side_effect=lambda body: captured_comments.append(body)
    )
    mock_issue.create_reaction = MagicMock(
        side_effect=lambda reaction: captured_reactions.append(reaction)
    )

    mock_repo = MagicMock()
    mock_repo.get_issue = MagicMock(return_value=mock_issue)

    mock_github = MagicMock()
    mock_github.get_repo = MagicMock(return_value=mock_repo)

    with patch('github.Github', return_value=mock_github):
        yield {
            'github': mock_github,
            'repo': mock_repo,
            'issue': mock_issue,
            'captured_comments': captured_comments,
            'captured_reactions': captured_reactions,
        }


def create_webhook_signature(payload: bytes, secret: str) -> str:
    """Create a GitHub webhook signature."""
    signature = hmac.new(
        secret.encode('utf-8'), msg=payload, digestmod=hashlib.sha256
    ).hexdigest()
    return f'sha256={signature}'


def create_issue_comment_payload(
    issue_number: int = 1,
    comment_body: str = '@openhands please fix this',
    repo_name: str = 'test-owner/test-repo',
    sender_id: int = TEST_GITHUB_USER_ID,
    sender_login: str = TEST_GITHUB_USERNAME,
    installation_id: int = 123456,
) -> dict[str, Any]:
    """Create a GitHub issue comment webhook payload."""
    owner, repo = repo_name.split('/')
    return {
        'action': 'created',
        'issue': {
            'number': issue_number,
            'title': 'Test Issue',
            'body': 'This is a test issue',
            'html_url': f'https://github.com/{repo_name}/issues/{issue_number}',
            'user': {'login': sender_login, 'id': sender_id},
        },
        'comment': {
            'id': 12345,
            'body': comment_body,
            'user': {'login': sender_login, 'id': sender_id},
            'html_url': f'https://github.com/{repo_name}/issues/{issue_number}#issuecomment-12345',
        },
        'repository': {
            'id': 12345678,
            'name': repo,
            'full_name': repo_name,
            'private': False,
            'html_url': f'https://github.com/{repo_name}',
            'owner': {
                'login': owner,
                'id': 99999,
            },
        },
        'sender': {'login': sender_login, 'id': sender_id},
        'installation': {'id': installation_id},
    }


@pytest.fixture
def issue_comment_payload():
    """Create a standard issue comment payload."""
    return create_issue_comment_payload()


@pytest.fixture
def trajectory_path():
    """Path to trajectory files."""
    return Path(__file__).parent / 'fixtures' / 'trajectories'


# Note: TestLLM injection will be handled separately as it requires
# the SDK to be installed and configured properly
