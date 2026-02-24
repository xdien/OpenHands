import os

from pydantic import SecretStr

from openhands.integrations.bitbucket_data_center.service import (
    BitbucketDCBranchesMixin,
    BitbucketDCFeaturesMixin,
    BitbucketDCPRsMixin,
    BitbucketDCReposMixin,
    BitbucketDCResolverMixin,
)
from openhands.integrations.service_types import (
    BaseGitService,
    GitService,
    InstallationsService,
    ProviderType,
)
from openhands.utils.import_utils import get_impl


class BitbucketDataCenterService(
    BitbucketDCBranchesMixin,
    BitbucketDCFeaturesMixin,
    BitbucketDCPRsMixin,
    BitbucketDCReposMixin,
    BitbucketDCResolverMixin,
    BaseGitService,
    GitService,
    InstallationsService,
):
    """Implementation of GitService for Bitbucket Data Center (self-hosted).

    Uses Bitbucket Server REST API 1.0 at https://{domain}/rest/api/1.0.
    The domain is always taken from ProviderToken.host — there is no default
    domain since Data Center instances are self-hosted.
    """

    def __init__(
        self,
        user_id: str | None = None,
        external_auth_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ) -> None:
        self.external_token_manager = external_token_manager
        self.external_auth_id = external_auth_id
        self.external_auth_token = external_auth_token

        # Normalize domain: strip any protocol prefix and trailing slashes
        domain = base_domain or ''
        domain = domain.strip()
        domain = domain.replace('https://', '').replace('http://', '')
        domain = domain.rstrip('/')

        self.base_domain = domain
        self.BASE_URL = f'https://{domain}/rest/api/1.0' if domain else ''

        if token:
            self.token = token

        # Derive user_id from the username portion of the token when not explicitly
        # provided. Only HTTP Access tokens (in username:access_token format) are
        # supported; plain passwords will not work with Bearer auth.
        if not user_id and token:
            token_val = token.get_secret_value()
            if ':' in token_val and not token_val.startswith('x-token-auth:'):
                user_id = token_val.split(':', 1)[0]
        self.user_id = user_id

    @property
    def provider(self) -> str:
        return ProviderType.BITBUCKET_DATA_CENTER.value


bitbucket_data_center_service_cls = os.environ.get(
    'OPENHANDS_BITBUCKET_DATA_CENTER_SERVICE_CLS',
    'openhands.integrations.bitbucket_data_center.bitbucket_data_center_service.BitbucketDataCenterService',
)

# Lazy loading to avoid circular imports
_bitbucket_data_center_service_impl = None


def get_bitbucket_data_center_service_impl():
    """Get the Bitbucket Data Center service implementation with lazy loading."""
    global _bitbucket_data_center_service_impl
    if _bitbucket_data_center_service_impl is None:
        _bitbucket_data_center_service_impl = get_impl(
            BitbucketDataCenterService, bitbucket_data_center_service_cls
        )
    return _bitbucket_data_center_service_impl


class _BitbucketDataCenterServiceImplProxy:
    """Proxy class to provide lazy loading for BitbucketDataCenterServiceImpl."""

    def __getattr__(self, name):
        impl = get_bitbucket_data_center_service_impl()
        return getattr(impl, name)

    def __call__(self, *args, **kwargs):
        impl = get_bitbucket_data_center_service_impl()
        return impl(*args, **kwargs)


BitbucketDataCenterServiceImpl: type[BitbucketDataCenterService] = (
    _BitbucketDataCenterServiceImplProxy()  # type: ignore[assignment]
)
