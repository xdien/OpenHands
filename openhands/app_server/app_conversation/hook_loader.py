"""Utilities for loading hooks for V1 conversations.

This module provides functions to load hooks from the agent-server,
which centralizes all hook loading logic. The app-server acts as a
thin proxy that calls the agent-server's /api/hooks endpoint.

All hook loading is handled by the agent-server.
"""

import logging

import httpx

from openhands.sdk.hooks import HookConfig

_logger = logging.getLogger(__name__)


def get_project_dir_for_hooks(
    working_dir: str,
    selected_repository: str | None = None,
) -> str:
    """Get the project directory path for loading hooks.

    When a repository is selected, hooks are loaded from
    {working_dir}/{repo_name}/.openhands/hooks.json.
    Otherwise, hooks are loaded from {working_dir}/.openhands/hooks.json.

    Args:
        working_dir: Base working directory path in the sandbox
        selected_repository: Repository name (e.g., 'OpenHands/software-agent-sdk')
            If provided, the repo name is appended to working_dir.

    Returns:
        The project directory path where hooks.json should be located.
    """
    if selected_repository:
        repo_name = selected_repository.split('/')[-1]
        return f'{working_dir}/{repo_name}'
    return working_dir


async def load_hooks_from_agent_server(
    agent_server_url: str,
    session_api_key: str | None,
    project_dir: str,
    httpx_client: httpx.AsyncClient,
) -> HookConfig | None:
    """Load hooks from the agent-server.

    This function makes a single API call to the agent-server's /api/hooks
    endpoint to load hooks from the workspace's .openhands/hooks.json file.

    Args:
        agent_server_url: URL of the agent server (e.g., 'http://localhost:8000')
        session_api_key: Session API key for authentication (optional)
        project_dir: Workspace directory path for project hooks
        httpx_client: Shared HTTP client for making the request

    Returns:
        HookConfig if hooks.json exists and is valid, None otherwise.
    """
    _logger.info(
        f'load_hooks_from_agent_server called: '
        f'agent_server_url={agent_server_url}, project_dir={project_dir}'
    )
    try:
        # Build request payload
        payload = {
            'project_dir': project_dir,
        }

        # Build headers
        headers = {'Content-Type': 'application/json'}
        if session_api_key:
            headers['X-Session-API-Key'] = session_api_key

        # Make API request
        response = await httpx_client.post(
            f'{agent_server_url}/api/hooks',
            json=payload,
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()

        data = response.json()

        # Extract hook_config from response
        hook_config_data = data.get('hook_config')
        if hook_config_data is None:
            _logger.debug('No hooks found in workspace')
            return None

        # Convert response to HookConfig
        hook_config = HookConfig.from_dict(hook_config_data)

        if hook_config.is_empty():
            _logger.debug('Hooks config is empty')
            return None

        _logger.info(f'Loaded hooks from agent-server for {project_dir}')
        return hook_config

    except httpx.HTTPStatusError as e:
        _logger.warning(
            f'Agent-server at {agent_server_url} returned error status {e.response.status_code} '
            f'when loading hooks from {project_dir}: {e.response.text}'
        )
        return None
    except httpx.RequestError as e:
        _logger.warning(
            f'Failed to connect to agent-server at {agent_server_url} '
            f'when loading hooks from {project_dir}: {e}'
        )
        return None
    except Exception as e:
        _logger.warning(
            f'Failed to load hooks from agent-server at {agent_server_url} '
            f'for project {project_dir}: {e}'
        )
        return None
