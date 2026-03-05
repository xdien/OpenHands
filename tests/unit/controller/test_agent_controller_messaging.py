"""Unit tests for AgentController messaging integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.controller.agent_controller import AgentController
from openhands.core.schema import AgentState
from openhands.events import EventSource, EventStream
from openhands.events.action import (
    ActionConfirmationStatus,
    CmdRunAction,
)
from openhands.storage.memory import InMemoryFileStore


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_messaging_service():
    """Create a mock messaging service."""
    service = AsyncMock()
    service.request_confirmation = AsyncMock()
    service.send_task_result = AsyncMock()
    return service


@pytest.fixture
def mock_agent():
    """Create a mock agent."""
    from openhands.events.action.message import SystemMessageAction

    agent = MagicMock()
    agent.name = 'test-agent'
    agent.sandbox_plugins = []
    agent.config = MagicMock()
    agent.config.cli_mode = False
    agent.llm = MagicMock()
    agent.llm.config = MagicMock()
    agent.llm.config.max_message_chars = 1000
    agent.prompt_manager = MagicMock()

    # Mock get_system_message
    system_message = SystemMessageAction(content='Test system message', tools=[])
    system_message._source = EventSource.AGENT
    agent.get_system_message.return_value = system_message

    return agent


@pytest.fixture
def event_stream():
    """Create an event stream."""
    return EventStream(sid='test-conversation', file_store=InMemoryFileStore({}))


@pytest.fixture
def conversation_stats():
    """Create mock conversation stats."""

    stats = MagicMock()
    stats.register_llm = MagicMock()
    return stats


class TestAgentControllerMessagingIntegration:
    """Tests for AgentController messaging integration."""

    @pytest.mark.asyncio
    async def test_messaging_service_stored_in_controller(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that messaging service is stored in controller."""
        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
        )

        assert controller._messaging_service == mock_messaging_service
        assert controller._external_user_id == 'telegram-123'

    @pytest.mark.asyncio
    async def test_send_confirmation_request_called(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that confirmation request is sent via messaging service."""
        from openhands.messaging.stores.confirmation_store import ConfirmationStatus

        mock_messaging_service.request_confirmation.return_value = (
            ConfirmationStatus.CONFIRMED
        )

        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
            confirmation_mode=True,
        )

        # Create an action requiring confirmation
        action = CmdRunAction(command='rm -rf /')
        action.confirmation_state = ActionConfirmationStatus.AWAITING_CONFIRMATION

        # Call the confirmation request method
        await controller._send_confirmation_request(action)

        # Verify messaging service was called
        mock_messaging_service.request_confirmation.assert_called_once()
        call_args = mock_messaging_service.request_confirmation.call_args
        assert call_args.kwargs['external_user_id'] == 'telegram-123'
        assert call_args.kwargs['action_type'] == 'CmdRunAction'
        assert call_args.kwargs['conversation_id'] == 'test-session'

    @pytest.mark.asyncio
    async def test_send_task_completion_notification_finished(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that task completion notification is sent for FINISHED state."""
        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
        )

        await controller._send_task_completion_notification('FINISHED')

        mock_messaging_service.send_task_result.assert_called_once()
        call_args = mock_messaging_service.send_task_result.call_args
        assert call_args.kwargs['external_user_id'] == 'telegram-123'
        assert call_args.kwargs['conversation_id'] == 'test-session'
        assert call_args.kwargs['state'] == 'FINISHED'

    @pytest.mark.asyncio
    async def test_send_task_completion_notification_error(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that task completion notification is sent for ERROR state."""
        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
        )

        # Set an error in state
        controller.state.last_error = 'Test error message'

        await controller._send_task_completion_notification('ERROR')

        mock_messaging_service.send_task_result.assert_called_once()
        call_args = mock_messaging_service.send_task_result.call_args
        assert call_args.kwargs['state'] == 'ERROR'
        assert call_args.kwargs['reason'] == 'Test error message'

    @pytest.mark.asyncio
    async def test_no_notification_without_messaging_service(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that no notification is sent if messaging service is not set."""
        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=None,
            external_user_id=None,
        )

        # Should not raise an exception
        await controller._send_task_completion_notification('FINISHED')

        # Messaging service should not be called
        mock_messaging_service.send_task_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notification_without_external_user_id(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that no notification is sent if external user ID is not set."""
        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id=None,
        )

        # Should not raise an exception
        await controller._send_task_completion_notification('FINISHED')

        # Messaging service should not be called
        mock_messaging_service.send_task_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmation_sets_agent_state_confirmed(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that confirmation response sets agent state to USER_CONFIRMED."""
        from openhands.messaging.stores.confirmation_store import ConfirmationStatus

        mock_messaging_service.request_confirmation.return_value = (
            ConfirmationStatus.CONFIRMED
        )

        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
            confirmation_mode=True,
        )

        action = CmdRunAction(command='ls -la')
        action.confirmation_state = ActionConfirmationStatus.AWAITING_CONFIRMATION

        await controller._send_confirmation_request(action)

        assert controller.state.agent_state == AgentState.USER_CONFIRMED

    @pytest.mark.asyncio
    async def test_confirmation_sets_agent_state_rejected(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that rejection response sets agent state to USER_REJECTED."""
        from openhands.messaging.stores.confirmation_store import ConfirmationStatus

        mock_messaging_service.request_confirmation.return_value = (
            ConfirmationStatus.REJECTED
        )

        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
            confirmation_mode=True,
        )

        action = CmdRunAction(command='rm -rf /')
        action.confirmation_state = ActionConfirmationStatus.AWAITING_CONFIRMATION

        await controller._send_confirmation_request(action)

        assert controller.state.agent_state == AgentState.USER_REJECTED

    @pytest.mark.asyncio
    async def test_confirmation_expired_sets_rejected(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that expired confirmation sets agent state to USER_REJECTED."""
        from openhands.messaging.stores.confirmation_store import ConfirmationStatus

        mock_messaging_service.request_confirmation.return_value = (
            ConfirmationStatus.EXPIRED
        )

        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
            confirmation_mode=True,
        )

        action = CmdRunAction(command='rm -rf /')
        action.confirmation_state = ActionConfirmationStatus.AWAITING_CONFIRMATION

        await controller._send_confirmation_request(action)

        assert controller.state.agent_state == AgentState.USER_REJECTED

    @pytest.mark.asyncio
    async def test_messaging_exception_handled_gracefully(
        self, mock_agent, event_stream, conversation_stats, mock_messaging_service
    ):
        """Test that messaging exceptions are handled gracefully."""
        mock_messaging_service.request_confirmation.side_effect = Exception(
            'Network error'
        )

        controller = AgentController(
            agent=mock_agent,
            event_stream=event_stream,
            conversation_stats=conversation_stats,
            iteration_delta=100,
            sid='test-session',
            messaging_service=mock_messaging_service,
            external_user_id='telegram-123',
            confirmation_mode=True,
        )

        action = CmdRunAction(command='ls -la')
        action.confirmation_state = ActionConfirmationStatus.AWAITING_CONFIRMATION

        # Should not raise an exception
        await controller._send_confirmation_request(action)

        # State should remain unchanged (not set to confirmed or rejected)
        # Initial state is LOADING when controller is created
        assert controller.state.agent_state == AgentState.LOADING
