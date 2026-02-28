from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from server.auth.token_manager import TokenManager
from storage.offline_token_store import OfflineTokenStore
from storage.stored_offline_token import StoredOfflineToken

from openhands.core.config.openhands_config import OpenHandsConfig


@pytest.fixture
def mock_config():
    return MagicMock(spec=OpenHandsConfig)


@pytest.fixture
def mock_async_session():
    """Create an async mock session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_async_session_maker(mock_async_session):
    """Create an async mock session maker."""
    session_maker = AsyncMock()
    session_maker.return_value.__aenter__.return_value = mock_async_session
    session_maker.return_value.__aexit__.return_value = None
    return session_maker


@pytest.fixture
def token_store(mock_async_session_maker, mock_config):
    return OfflineTokenStore('test_user_id', mock_async_session_maker, mock_config)


@pytest.fixture
def token_manager():
    with patch('server.config.get_config') as mock_get_config:
        mock_config = mock_get_config.return_value
        mock_config.jwt_secret.get_secret_value.return_value = 'test_secret'
        return TokenManager(external=False)


@pytest.mark.asyncio
async def test_store_token_new_record(token_store, mock_async_session):
    # Setup
    test_token = 'test_offline_token'
    mock_async_session.query.return_value.filter.return_value.first.return_value = None

    # Execute
    await token_store.store_token(test_token)

    # Verify
    mock_async_session.add.assert_called_once()
    mock_async_session.commit.assert_called_once()
    added_record = mock_async_session.add.call_args[0][0]
    assert isinstance(added_record, StoredOfflineToken)
    assert added_record.user_id == 'test_user_id'
    assert added_record.offline_token == test_token


@pytest.mark.asyncio
async def test_store_token_existing_record(token_store, mock_async_session):
    # Setup
    existing_record = StoredOfflineToken(
        user_id='test_user_id', offline_token='old_token'
    )
    mock_async_session.query.return_value.filter.return_value.first.return_value = (
        existing_record
    )
    test_token = 'new_offline_token'

    # Execute
    await token_store.store_token(test_token)

    # Verify
    mock_async_session.add.assert_not_called()
    mock_async_session.commit.assert_called_once()
    assert existing_record.offline_token == test_token


@pytest.mark.asyncio
async def test_load_token_existing(token_store, mock_async_session):
    # Setup
    test_token = 'test_offline_token'
    mock_async_session.query.return_value.filter.return_value.first.return_value = (
        StoredOfflineToken(user_id='test_user_id', offline_token=test_token)
    )

    # Execute
    result = await token_store.load_token()

    # Verify
    assert result == test_token


@pytest.mark.asyncio
async def test_load_token_not_found(token_store, mock_async_session):
    # Setup
    mock_async_session.query.return_value.filter.return_value.first.return_value = None

    # Execute
    result = await token_store.load_token()

    # Verify
    assert result is None


@pytest.mark.asyncio
async def test_get_instance(mock_config):
    # Setup
    test_user_id = 'test_user_id'

    # Execute
    result = await OfflineTokenStore.get_instance(mock_config, test_user_id)

    # Verify
    assert isinstance(result, OfflineTokenStore)
    assert result.user_id == test_user_id
    assert result.config == mock_config


def test_load_store_org_token(token_manager, mock_async_session_maker):
    with patch('server.auth.token_manager.a_session_maker', mock_async_session_maker):
        token_manager.store_org_token('some-org-id', 'some-token')
        assert token_manager.load_org_token('some-org-id') == 'some-token'
