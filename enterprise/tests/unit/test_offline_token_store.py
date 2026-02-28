import pytest
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from storage.offline_token_store import OfflineTokenStore
from storage.stored_offline_token import StoredOfflineToken
from openhands.core.config.openhands_config import OpenHandsConfig


@pytest.fixture
def mock_config():
    return None  # Not used in tests


@pytest.mark.asyncio
async def test_store_token_new_record(async_session_maker, mock_config):
    # Setup
    token_store = OfflineTokenStore('test_user_id', async_session_maker, mock_config)
    test_token = 'test_offline_token'

    # Execute
    await token_store.store_token(test_token)

    # Verify - use a new session to query
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(StoredOfflineToken).where(StoredOfflineToken.user_id == 'test_user_id')
        )
        record = result.scalar_one_or_none()
        assert record is not None
        assert record.user_id == 'test_user_id'
        assert record.offline_token == test_token


@pytest.mark.asyncio
async def test_store_token_existing_record(async_session_maker, mock_config):
    # Setup - create existing record
    token_store = OfflineTokenStore('test_user_id', async_session_maker, mock_config)
    
    async with async_session_maker() as session:
        session.add(
            StoredOfflineToken(user_id='test_user_id', offline_token='old_token')
        )
        await session.commit()

    test_token = 'new_offline_token'

    # Execute
    await token_store.store_token(test_token)

    # Verify
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(StoredOfflineToken).where(StoredOfflineToken.user_id == 'test_user_id')
        )
        record = result.scalar_one_or_none()
        assert record is not None
        assert record.offline_token == test_token


@pytest.mark.asyncio
async def test_load_token_existing(async_session_maker, mock_config):
    # Setup - create existing record
    token_store = OfflineTokenStore('test_user_id', async_session_maker, mock_config)
    
    async with async_session_maker() as session:
        session.add(
            StoredOfflineToken(user_id='test_user_id', offline_token='test_offline_token')
        )
        await session.commit()

    # Execute
    result = await token_store.load_token()

    # Verify
    assert result == 'test_offline_token'


@pytest.mark.asyncio
async def test_load_token_not_found(async_session_maker, mock_config):
    # Setup
    token_store = OfflineTokenStore('nonexistent_user', async_session_maker, mock_config)

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
