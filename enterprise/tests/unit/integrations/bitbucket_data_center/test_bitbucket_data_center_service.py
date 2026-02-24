"""Unit tests for SaaSBitbucketDataCenterService."""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from integrations.bitbucket_data_center.bitbucket_data_center_service import (
    SaaSBitbucketDataCenterService,
)


class TestSaaSBitbucketDataCenterServiceInit:
    """Tests for __init__ domain derivation logic."""

    def test_explicit_base_domain_is_used(self):
        """Provided base_domain takes precedence over token URL derivation."""
        with patch(
            'integrations.bitbucket_data_center.bitbucket_data_center_service.BITBUCKET_DATA_CENTER_TOKEN_URL',
            'https://bitbucket.example.com/rest/oauth2/latest/token',
        ):
            service = SaaSBitbucketDataCenterService(
                base_domain='custom.bitbucket.host'
            )
        assert service.base_domain == 'custom.bitbucket.host'
        assert 'custom.bitbucket.host' in service.BASE_URL

    def test_base_domain_derived_from_token_url_when_not_provided(self):
        """base_domain is derived from BITBUCKET_DATA_CENTER_TOKEN_URL when not given."""
        with patch(
            'integrations.bitbucket_data_center.bitbucket_data_center_service.BITBUCKET_DATA_CENTER_TOKEN_URL',
            'https://bitbucket.example.com/rest/oauth2/latest/token',
        ):
            service = SaaSBitbucketDataCenterService()
        assert service.base_domain == 'bitbucket.example.com'
        assert service.BASE_URL == 'https://bitbucket.example.com/rest/api/1.0'

    def test_empty_base_domain_when_token_url_not_set(self):
        """BASE_URL is empty string when neither base_domain nor token URL are set."""
        with patch(
            'integrations.bitbucket_data_center.bitbucket_data_center_service.BITBUCKET_DATA_CENTER_TOKEN_URL',
            '',
        ):
            service = SaaSBitbucketDataCenterService()
        assert service.base_domain == ''
        assert service.BASE_URL == ''


class TestSaaSBitbucketDataCenterServiceGetLatestToken:
    """Tests for get_latest_token() auth paths."""

    @pytest.mark.asyncio
    async def test_get_token_via_external_auth_token(self):
        """Returns token retrieved via external auth (access) token."""
        service = SaaSBitbucketDataCenterService(
            external_auth_token=SecretStr('access-token-value'),
            base_domain='bitbucket.example.com',
        )
        with patch.object(
            service.token_manager,
            'get_idp_token',
            new_callable=AsyncMock,
            return_value='dc-token-from-access',
        ) as mock_get:
            result = await service.get_latest_token()

        mock_get.assert_awaited_once()
        assert result is not None
        assert result.get_secret_value() == 'x-token-auth:dc-token-from-access'

    @pytest.mark.asyncio
    async def test_get_token_via_external_auth_id(self):
        """Returns token retrieved via offline token (external_auth_id path)."""
        service = SaaSBitbucketDataCenterService(
            external_auth_id='user-keycloak-id',
            base_domain='bitbucket.example.com',
        )
        with (
            patch.object(
                service.token_manager,
                'load_offline_token',
                new_callable=AsyncMock,
                return_value='offline-token',
            ),
            patch.object(
                service.token_manager,
                'get_idp_token_from_offline_token',
                new_callable=AsyncMock,
                return_value='dc-token-from-offline',
            ) as mock_get,
        ):
            result = await service.get_latest_token()

        mock_get.assert_awaited_once()
        assert result is not None
        assert result.get_secret_value() == 'x-token-auth:dc-token-from-offline'

    @pytest.mark.asyncio
    async def test_get_token_via_user_id(self):
        """Returns token retrieved via user_id (IDP user ID path)."""
        service = SaaSBitbucketDataCenterService(
            user_id='internal-user-id',
            base_domain='bitbucket.example.com',
        )
        with patch.object(
            service.token_manager,
            'get_idp_token_from_idp_user_id',
            new_callable=AsyncMock,
            return_value='dc-token-from-user-id',
        ) as mock_get:
            result = await service.get_latest_token()

        mock_get.assert_awaited_once()
        assert result is not None
        assert result.get_secret_value() == 'x-token-auth:dc-token-from-user-id'

    @pytest.mark.asyncio
    async def test_returns_none_when_no_auth_info(self):
        """Returns None and logs a warning when no auth info is set."""
        service = SaaSBitbucketDataCenterService(
            base_domain='bitbucket.example.com',
        )
        result = await service.get_latest_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_latest_token_updates_self_token(self):
        """get_latest_token() persists the new token to self.token for retry use."""
        service = SaaSBitbucketDataCenterService(
            external_auth_token=SecretStr('access-token-value'),
            base_domain='bitbucket.example.com',
        )
        with patch.object(
            service.token_manager,
            'get_idp_token',
            new_callable=AsyncMock,
            return_value='refreshed-dc-token',
        ):
            result = await service.get_latest_token()

        assert result is not None
        assert result.get_secret_value() == 'x-token-auth:refreshed-dc-token'
        assert service.token is not None
        assert service.token.get_secret_value() == 'x-token-auth:refreshed-dc-token'


class TestSaaSBitbucketDataCenterServiceRefresh:
    """Tests for refresh flag behaviour."""

    def test_refresh_is_true_by_default(self):
        """self.refresh must be True so the 401-retry path in _make_request is enabled."""
        service = SaaSBitbucketDataCenterService(
            base_domain='bitbucket.example.com',
        )
        assert service.refresh is True
