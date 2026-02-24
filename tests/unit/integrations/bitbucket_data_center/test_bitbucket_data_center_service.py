"""Tests for BitbucketDataCenterService."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.integrations.bitbucket_data_center.bitbucket_data_center_service import (
    BitbucketDataCenterService,
)
from openhands.integrations.service_types import OwnerType, User


# ── init / BASE_URL ───────────────────────────────────────────────────────────


def test_init_strips_protocol_and_slash():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='https://host.example.com/'
    )
    assert svc.BASE_URL == 'https://host.example.com/rest/api/1.0'
    assert svc.base_domain == 'host.example.com'


def test_init_plain_domain():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='host.example.com'
    )
    assert svc.BASE_URL == 'https://host.example.com/rest/api/1.0'


# ── user_id derivation ────────────────────────────────────────────────────────


def test_user_id_derived_from_username_password_token():
    svc = BitbucketDataCenterService(token=SecretStr('alice:secret'))
    assert svc.user_id == 'alice'


def test_user_id_not_derived_from_xtoken_auth_token():
    svc = BitbucketDataCenterService(token=SecretStr('x-token-auth:mytoken'))
    assert svc.user_id is None


def test_explicit_user_id_not_overridden():
    svc = BitbucketDataCenterService(token=SecretStr('alice:secret'), user_id='bob')
    assert svc.user_id == 'bob'


# ── _get_headers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_headers_username_token():
    svc = BitbucketDataCenterService(
        token=SecretStr('user:pass'), base_domain='host.example.com'
    )
    headers = await svc._get_headers()
    assert headers['Authorization'] == 'Bearer pass'


@pytest.mark.asyncio
async def test_get_headers_token_only_format():
    svc = BitbucketDataCenterService(
        token=SecretStr('x-token-auth:plaintoken'), base_domain='host.example.com'
    )
    headers = await svc._get_headers()
    assert headers['Authorization'] == 'Bearer plaintoken'



# ── get_user ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_with_user_id():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'),
        base_domain='host.example.com',
        user_id='jdoe',
    )
    mock_response = {
        'values': [
            {
                'id': 5,
                'slug': 'jdoe',
                'displayName': 'J Doe',
                'emailAddress': 'j@example.com',
            }
        ]
    }
    with patch.object(svc, '_make_request', return_value=(mock_response, {})):
        user = await svc.get_user()

    assert user.id == '5'
    assert user.login == 'jdoe'
    assert user.name == 'J Doe'
    assert user.email == 'j@example.com'


@pytest.mark.asyncio
async def test_get_user_without_user_id():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='host.example.com'
    )
    # No user_id set — should return minimal user without API call
    with patch.object(svc, '_make_request') as mock_req:
        user = await svc.get_user()
        mock_req.assert_not_called()

    assert isinstance(user, User)
    assert user.id == ''
    assert user.login == ''


@pytest.mark.asyncio
async def test_get_user_lookup_failure_returns_fallback():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'),
        base_domain='host.example.com',
        user_id='jdoe',
    )
    with patch.object(svc, '_make_request', side_effect=Exception('timeout')):
        user = await svc.get_user()

    assert user.id == 'jdoe'
    assert user.login == 'jdoe'


# ── _parse_repository ─────────────────────────────────────────────────────────


def test_parse_repository_full():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='host.example.com'
    )
    repo_data = {
        'id': 42,
        'slug': 'myrepo',
        'project': {'key': 'PROJ'},
        'public': True,
        'defaultBranch': 'main',
    }
    repo = svc._parse_repository(repo_data)

    assert repo.full_name == 'PROJ/myrepo'
    assert repo.owner_type == OwnerType.ORGANIZATION
    assert repo.is_public is True
    assert repo.main_branch == 'main'


def test_parse_repository_missing_key_raises():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='host.example.com'
    )
    with pytest.raises(ValueError, match='missing project key or slug'):
        svc._parse_repository({'id': 1, 'project': {}, 'slug': ''})


# ── verify_access ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_access_makes_request():
    svc = BitbucketDataCenterService(
        token=SecretStr('tok'), base_domain='host.example.com'
    )
    with patch.object(svc, '_make_request', return_value=({}, {})) as mock_req:
        await svc.verify_access()

    mock_req.assert_called_once()
    call_url = mock_req.call_args[0][0]
    assert call_url.endswith('/repos')
