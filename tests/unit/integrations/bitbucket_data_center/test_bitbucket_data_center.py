"""Tests for BitbucketDataCenterPRHandler."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.resolver.interfaces.bitbucket_data_center import (
    BitbucketDataCenterPRHandler,
)


@pytest.fixture
def handler():
    return BitbucketDataCenterPRHandler(
        owner='PROJ',
        repo='myrepo',
        token='x-token-auth:mytoken',
        username=None,
        base_domain='bitbucket.example.com',
    )


@pytest.fixture
def basic_auth_handler():
    return BitbucketDataCenterPRHandler(
        owner='PROJ',
        repo='myrepo',
        token='user:pass',
        username='user',
        base_domain='bitbucket.example.com',
    )


# ── init ──────────────────────────────────────────────────────────────────────


def test_init_bearer_token(handler):
    assert handler.base_domain == 'bitbucket.example.com'
    assert handler.base_url == 'https://bitbucket.example.com/rest/api/1.0'
    assert 'x-token-auth:mytoken@' in handler.clone_url
    expected = 'Basic ' + base64.b64encode(b'x-token-auth:mytoken').decode()
    assert handler.headers['Authorization'] == expected


def test_init_basic_auth_token(basic_auth_handler):
    assert 'user:pass@' in basic_auth_handler.clone_url
    expected = 'Basic ' + base64.b64encode(b'user:pass').decode()
    assert basic_auth_handler.headers['Authorization'] == expected


def test_init_domain_normalization():
    h = BitbucketDataCenterPRHandler(
        owner='PROJ', repo='r', token='t', base_domain='https://host.com/'
    )
    assert h.base_domain == 'host.com'
    assert h.base_url == 'https://host.com/rest/api/1.0'


# ── headers ───────────────────────────────────────────────────────────────────


def test_get_headers_basic_auth(basic_auth_handler):
    expected = 'Basic ' + base64.b64encode(b'user:pass').decode()
    assert basic_auth_handler.get_headers()['Authorization'] == expected


def test_get_headers_bearer(handler):
    expected = 'Basic ' + base64.b64encode(b'x-token-auth:mytoken').decode()
    assert handler.get_headers()['Authorization'] == expected


# ── URLs ──────────────────────────────────────────────────────────────────────


def test_get_clone_url_basic_auth(basic_auth_handler):
    assert (
        basic_auth_handler.clone_url
        == 'https://user:pass@bitbucket.example.com/scm/proj/myrepo.git'
    )


def test_get_clone_url_bearer(handler):
    assert (
        handler.clone_url
        == 'https://x-token-auth:mytoken@bitbucket.example.com/scm/proj/myrepo.git'
    )



def test_get_repo_url(handler):
    assert (
        handler.get_repo_url()
        == 'https://bitbucket.example.com/projects/PROJ/repos/myrepo'
    )


def test_get_pr_url(handler):
    assert (
        handler.get_pr_url(42)
        == 'https://bitbucket.example.com/projects/PROJ/repos/myrepo/pull-requests/42'
    )


# ── get_issue ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch('httpx.AsyncClient')
async def test_get_issue(mock_client_cls, handler):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        'id': 7,
        'title': 'Fix bug',
        'description': 'A description',
        'fromRef': {'displayId': 'feature/fix'},
        'toRef': {'displayId': 'main'},
    }
    mock_async_client = AsyncMock()
    mock_async_client.get.return_value = mock_response
    mock_client_cls.return_value.__aenter__.return_value = mock_async_client

    issue = await handler.get_issue(7)

    assert issue.number == 7
    assert issue.title == 'Fix bug'
    assert issue.body == 'A description'
    assert issue.head_branch == 'feature/fix'


# ── get_issue_comments ────────────────────────────────────────────────────────


@patch('httpx.get')
def test_get_issue_comments_single_page(mock_get, handler):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        'values': [
            {'action': 'COMMENTED', 'comment': {'id': 1, 'text': 'hello'}},
            {'action': 'OPENED'},
        ],
        'isLastPage': True,
    }
    mock_get.return_value = mock_response

    comments = handler.get_issue_comments(1)
    assert comments == ['hello']


@patch('httpx.get')
def test_get_issue_comments_pagination(mock_get, handler):
    page1 = MagicMock()
    page1.raise_for_status = MagicMock()
    page1.json.return_value = {
        'values': [{'action': 'COMMENTED', 'comment': {'id': 1, 'text': 'first'}}],
        'isLastPage': False,
        'nextPageStart': 1,
    }
    page2 = MagicMock()
    page2.raise_for_status = MagicMock()
    page2.json.return_value = {
        'values': [{'action': 'COMMENTED', 'comment': {'id': 2, 'text': 'second'}}],
        'isLastPage': True,
    }
    mock_get.side_effect = [page1, page2]

    comments = handler.get_issue_comments(1)
    assert comments == ['first', 'second']


# ── download_issues ───────────────────────────────────────────────────────────


@patch('httpx.get')
def test_download_issues_pagination(mock_get, handler):
    page1 = MagicMock()
    page1.raise_for_status = MagicMock()
    page1.json.return_value = {
        'values': [{'id': 1, 'title': 'PR 1'}],
        'isLastPage': False,
        'nextPageStart': 1,
    }
    page2 = MagicMock()
    page2.raise_for_status = MagicMock()
    page2.json.return_value = {
        'values': [{'id': 2, 'title': 'PR 2'}],
        'isLastPage': True,
    }
    mock_get.side_effect = [page1, page2]

    prs = handler.download_issues()
    assert len(prs) == 2
    assert prs[0]['id'] == 1
    assert prs[1]['id'] == 2


# ── create_pull_request ───────────────────────────────────────────────────────


@patch('httpx.post')
def test_create_pull_request(mock_post, handler):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        'id': 10,
        'links': {'self': [{'href': 'https://bitbucket.example.com/projects/PROJ/repos/myrepo/pull-requests/10'}]},
    }
    mock_post.return_value = mock_response

    result = handler.create_pull_request({
        'title': 'My PR',
        'description': 'desc',
        'source_branch': 'feature',
        'target_branch': 'main',
    })

    assert result['html_url'] == 'https://bitbucket.example.com/projects/PROJ/repos/myrepo/pull-requests/10'
    assert result['number'] == 10

    _, kwargs = mock_post.call_args
    payload = kwargs['json']
    assert payload['fromRef']['id'] == 'refs/heads/feature'
    assert payload['toRef']['id'] == 'refs/heads/main'
    assert payload['fromRef']['repository']['slug'] == 'myrepo'


# ── get_converted_issues ──────────────────────────────────────────────────────


def test_get_converted_issues_filters_by_id(handler):
    with patch.object(
        handler,
        'download_issues',
        return_value=[
            {'id': 1, 'title': 'PR 1', 'description': 'body', 'fromRef': {'displayId': 'f1'}, 'toRef': {'displayId': 'main'}},
            {'id': 2, 'title': 'PR 2', 'description': 'body2', 'fromRef': {'displayId': 'f2'}, 'toRef': {'displayId': 'main'}},
        ],
    ):
        issues = handler.get_converted_issues([1])

    assert len(issues) == 1
    assert issues[0].number == 1
    assert issues[0].head_branch == 'f1'


def test_get_converted_issues_raises_for_empty(handler):
    with pytest.raises(ValueError, match='Unspecified issue numbers'):
        handler.get_converted_issues([])

    with pytest.raises(ValueError, match='Unspecified issue numbers'):
        handler.get_converted_issues(None)


# ── branch_exists ─────────────────────────────────────────────────────────────


@patch('httpx.get')
def test_branch_exists_true(mock_get, handler):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        'values': [{'displayId': 'feature/my-branch'}]
    }
    mock_get.return_value = mock_response

    assert handler.branch_exists('feature/my-branch') is True


@patch('httpx.get')
def test_branch_exists_false(mock_get, handler):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {'values': []}
    mock_get.return_value = mock_response

    assert handler.branch_exists('nonexistent') is False


# ── get_default_branch_name ───────────────────────────────────────────────────


@patch('httpx.get')
def test_get_default_branch_name_fallback(mock_get, handler):
    mock_get.side_effect = Exception('network error')

    assert handler.get_default_branch_name() == 'main'


@patch('httpx.get')
def test_get_default_branch_name_logs_warning(mock_get, handler, caplog):
    import logging

    from openhands.core.logger import openhands_logger

    mock_get.side_effect = Exception('network error')

    # openhands_logger has propagate=False, so attach caplog's handler directly
    openhands_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger='openhands'):
            result = handler.get_default_branch_name()
    finally:
        openhands_logger.removeHandler(caplog.handler)

    assert result == 'main'
    assert any('Failed to get default branch' in r.message for r in caplog.records)


@patch('httpx.get')
def test_branch_exists_logs_warning_on_exception(mock_get, handler, caplog):
    import logging

    from openhands.core.logger import openhands_logger

    mock_get.side_effect = Exception('connection refused')

    openhands_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger='openhands'):
            result = handler.branch_exists('my-branch')
    finally:
        openhands_logger.removeHandler(caplog.handler)

    assert result is False
    assert any('Failed to check branch existence' in r.message for r in caplog.records)
