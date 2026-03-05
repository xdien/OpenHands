"""Unit tests for the messaging module."""

from datetime import datetime, timedelta

import pytest

from openhands.messaging.config import (
    MessagingConfig,
    MessagingProviderType,
    TelegramConfig,
)
from openhands.messaging.stores.confirmation_store import (
    ConfirmationStatus,
    InMemoryConfirmationStore,
    PendingConfirmation,
)
from openhands.messaging.stores.conversation_store import (
    InMemoryConversationStore,
    UserConversationMapping,
)


class TestConfirmationStatus:
    """Tests for ConfirmationStatus enum."""

    def test_confirmation_status_values(self):
        """Test that ConfirmationStatus has expected values."""
        assert ConfirmationStatus.PENDING == 'pending'
        assert ConfirmationStatus.CONFIRMED == 'confirmed'
        assert ConfirmationStatus.REJECTED == 'rejected'
        assert ConfirmationStatus.EXPIRED == 'expired'


class TestPendingConfirmation:
    """Tests for PendingConfirmation model."""

    def test_create_pending_confirmation(self):
        """Test creating a pending confirmation."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Run command: ls -la',
            action_content='CmdRunAction(command="ls -la")',
        )

        assert confirmation.id == 'test-id'
        assert confirmation.conversation_id == 'conv-123'
        assert confirmation.external_user_id == 'user-456'
        assert confirmation.action_type == 'CmdRunAction'
        assert confirmation.status == ConfirmationStatus.PENDING
        assert confirmation.callback_message_id is None

    def test_is_expired_false(self):
        """Test is_expired returns False when not expired."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
        )

        assert confirmation.is_expired is False

    def test_is_expired_true(self):
        """Test is_expired returns True when expired."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
            expires_at=datetime.utcnow() - timedelta(minutes=5),
        )

        assert confirmation.is_expired is True

    def test_is_pending_true(self):
        """Test is_pending returns True for pending confirmation."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
        )

        assert confirmation.is_pending is True

    def test_is_pending_false_when_confirmed(self):
        """Test is_pending returns False when confirmed."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
            status=ConfirmationStatus.CONFIRMED,
        )

        assert confirmation.is_pending is False

    def test_set_expiration(self):
        """Test setting expiration time."""
        confirmation = PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
        )

        confirmation.set_expiration(300)  # 5 minutes

        assert confirmation.expires_at is not None
        assert confirmation.expires_at > datetime.utcnow()


class TestInMemoryConfirmationStore:
    """Tests for InMemoryConfirmationStore."""

    @pytest.fixture
    def store(self):
        """Create a confirmation store."""
        return InMemoryConfirmationStore()

    @pytest.fixture
    def sample_confirmation(self):
        """Create a sample confirmation."""
        return PendingConfirmation(
            id='test-id',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test',
            action_content='Test',
        )

    @pytest.mark.asyncio
    async def test_create_confirmation(self, store, sample_confirmation):
        """Test creating a confirmation."""
        result = await store.create_confirmation(sample_confirmation)

        assert result == sample_confirmation

    @pytest.mark.asyncio
    async def test_get_confirmation(self, store, sample_confirmation):
        """Test getting a confirmation by ID."""
        await store.create_confirmation(sample_confirmation)

        result = await store.get_confirmation('test-id')

        assert result == sample_confirmation

    @pytest.mark.asyncio
    async def test_get_confirmation_not_found(self, store):
        """Test getting a non-existent confirmation."""
        result = await store.get_confirmation('nonexistent')

        assert result is None

    @pytest.mark.asyncio
    async def test_update_confirmation(self, store, sample_confirmation):
        """Test updating a confirmation."""
        await store.create_confirmation(sample_confirmation)

        sample_confirmation.status = ConfirmationStatus.CONFIRMED
        result = await store.update_confirmation(sample_confirmation)

        assert result.status == ConfirmationStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_delete_confirmation(self, store, sample_confirmation):
        """Test deleting a confirmation."""
        await store.create_confirmation(sample_confirmation)

        result = await store.delete_confirmation('test-id')

        assert result is True
        assert await store.get_confirmation('test-id') is None

    @pytest.mark.asyncio
    async def test_get_pending_confirmations_for_user(self, store):
        """Test getting pending confirmations for a user."""
        confirmation1 = PendingConfirmation(
            id='conf-1',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='CmdRunAction',
            action_details='Test 1',
            action_content='Test 1',
        )
        confirmation2 = PendingConfirmation(
            id='conf-2',
            conversation_id='conv-123',
            external_user_id='user-456',
            action_type='FileEditAction',
            action_details='Test 2',
            action_content='Test 2',
        )
        confirmation3 = PendingConfirmation(
            id='conf-3',
            conversation_id='conv-456',
            external_user_id='user-789',
            action_type='CmdRunAction',
            action_details='Test 3',
            action_content='Test 3',
        )

        await store.create_confirmation(confirmation1)
        await store.create_confirmation(confirmation2)
        await store.create_confirmation(confirmation3)

        results = await store.get_pending_confirmations_for_user('user-456')

        assert len(results) == 2
        assert all(c.external_user_id == 'user-456' for c in results)


class TestUserConversationMapping:
    """Tests for UserConversationMapping model."""

    def test_create_mapping(self):
        """Test creating a user conversation mapping."""
        mapping = UserConversationMapping(
            external_user_id='telegram-123',
            conversation_id='openhands-conv-456',
        )

        assert mapping.external_user_id == 'telegram-123'
        assert mapping.conversation_id == 'openhands-conv-456'
        assert mapping.is_active is True

    def test_touch_updates_timestamp(self):
        """Test that touch() updates the updated_at timestamp."""
        mapping = UserConversationMapping(
            external_user_id='telegram-123',
            conversation_id='openhands-conv-456',
        )

        old_time = mapping.updated_at
        mapping.touch()

        assert mapping.updated_at > old_time


class TestInMemoryConversationStore:
    """Tests for InMemoryConversationStore."""

    @pytest.fixture
    def store(self):
        """Create a conversation store."""
        return InMemoryConversationStore()

    @pytest.mark.asyncio
    async def test_create_mapping(self, store):
        """Test creating a mapping."""
        mapping = await store.create_mapping(
            external_user_id='telegram-123',
            conversation_id='conv-456',
        )

        assert mapping.external_user_id == 'telegram-123'
        assert mapping.conversation_id == 'conv-456'
        assert mapping.is_active is True

    @pytest.mark.asyncio
    async def test_get_mapping_by_external_user_id(self, store):
        """Test getting mapping by external user ID."""
        await store.create_mapping(
            external_user_id='telegram-123',
            conversation_id='conv-456',
        )

        mapping = await store.get_mapping_by_external_user_id('telegram-123')

        assert mapping is not None
        assert mapping.conversation_id == 'conv-456'

    @pytest.mark.asyncio
    async def test_get_mapping_by_conversation_id(self, store):
        """Test getting mapping by conversation ID."""
        await store.create_mapping(
            external_user_id='telegram-123',
            conversation_id='conv-456',
        )

        mapping = await store.get_mapping_by_conversation_id('conv-456')

        assert mapping is not None
        assert mapping.external_user_id == 'telegram-123'

    @pytest.mark.asyncio
    async def test_deactivate_mapping(self, store):
        """Test deactivating a mapping."""
        await store.create_mapping(
            external_user_id='telegram-123',
            conversation_id='conv-456',
        )

        result = await store.deactivate_mapping(external_user_id='telegram-123')

        assert result is True
        # After deactivation, the mapping is no longer returned by get_mapping_by_external_user_id
        # because the store removes it from the active index
        mapping = await store.get_mapping_by_external_user_id('telegram-123')
        assert mapping is None

        # Deactivating a non-existent mapping returns False
        result = await store.deactivate_mapping(external_user_id='nonexistent')
        assert result is False


class TestMessagingConfig:
    """Tests for MessagingConfig model."""

    def test_default_config(self):
        """Test default messaging config is disabled."""
        config = MessagingConfig()

        assert config.enabled is False
        assert config.provider == MessagingProviderType.TELEGRAM

    def test_enabled_config(self):
        """Test enabled messaging config."""
        config = MessagingConfig(
            enabled=True,
            provider=MessagingProviderType.TELEGRAM,
            allowed_user_ids=['123456789'],
            provider_config={'bot_token': 'test_token'},
        )

        assert config.enabled is True
        assert '123456789' in config.allowed_user_ids

    def test_get_telegram_config(self):
        """Test getting Telegram config from provider_config."""
        config = MessagingConfig(
            enabled=True,
            provider=MessagingProviderType.TELEGRAM,
            provider_config={
                'bot_token': 'test_token',
                'poll_interval': 2,
            },
        )

        telegram_config = config.get_telegram_config()

        # bot_token is a SecretStr, use get_secret_value() to compare
        assert telegram_config.bot_token.get_secret_value() == 'test_token'
        assert telegram_config.poll_interval == 2


class TestTelegramConfig:
    """Tests for TelegramConfig model."""

    def test_default_telegram_config(self):
        """Test default Telegram config."""
        config = TelegramConfig(bot_token='test_token')

        # bot_token is a SecretStr, use get_secret_value() to compare
        assert config.bot_token.get_secret_value() == 'test_token'
        assert config.poll_interval == 1
        assert config.webhook_url is None

    def test_webhook_config(self):
        """Test Telegram config with webhook."""
        config = TelegramConfig(
            bot_token='test_token',
            webhook_url='https://example.com/webhook',
        )

        assert config.webhook_url == 'https://example.com/webhook'
