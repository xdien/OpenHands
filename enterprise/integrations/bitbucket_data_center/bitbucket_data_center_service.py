from urllib.parse import urlparse

from pydantic import SecretStr

from openhands.core.logger import openhands_logger as logger
from openhands.integrations.bitbucket_data_center.bitbucket_data_center_service import (
    BitbucketDataCenterService,
)
from openhands.integrations.service_types import ProviderType
from server.auth.constants import BITBUCKET_DATA_CENTER_TOKEN_URL
from server.auth.token_manager import TokenManager


class SaaSBitbucketDataCenterService(BitbucketDataCenterService):
    def __init__(
        self,
        user_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_auth_id: str | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        # Derive base_domain from BITBUCKET_DATA_CENTER_TOKEN_URL if not provided
        if not base_domain and BITBUCKET_DATA_CENTER_TOKEN_URL:
            base_domain = urlparse(BITBUCKET_DATA_CENTER_TOKEN_URL).netloc

        logger.info(
            f'SaaSBitbucketDataCenterService created with user_id {user_id}, '
            f'external_auth_id {external_auth_id}, '
            f'external_auth_token {"set" if external_auth_token else "None"}, '
            f'token {"set" if token else "None"}, '
            f'external_token_manager {external_token_manager}, '
            f'base_domain {base_domain}'
        )
        super().__init__(
            user_id=user_id,
            external_auth_token=external_auth_token,
            external_auth_id=external_auth_id,
            token=token,
            external_token_manager=external_token_manager,
            base_domain=base_domain,
        )
        self.refresh = True

        self.external_auth_token = external_auth_token
        self.external_auth_id = external_auth_id
        self.token_manager = TokenManager(external=external_token_manager)

    async def get_latest_token(self) -> SecretStr | None:
        bitbucket_dc_token = None
        if self.external_auth_token:
            bitbucket_dc_token = SecretStr(
                await self.token_manager.get_idp_token(
                    self.external_auth_token.get_secret_value(),
                    idp=ProviderType.BITBUCKET_DATA_CENTER,
                )
            )
            logger.debug(
                f'Got Bitbucket DC token {bitbucket_dc_token} from access token: {self.external_auth_token}'
            )
        elif self.external_auth_id:
            offline_token = await self.token_manager.load_offline_token(
                self.external_auth_id
            )
            bitbucket_dc_token = SecretStr(
                await self.token_manager.get_idp_token_from_offline_token(
                    offline_token, ProviderType.BITBUCKET_DATA_CENTER
                )
            )
            logger.info(
                f'Got Bitbucket DC token {bitbucket_dc_token.get_secret_value()} from external auth user ID: {self.external_auth_id}'
            )
        elif self.user_id:
            bitbucket_dc_token = SecretStr(
                await self.token_manager.get_idp_token_from_idp_user_id(
                    self.user_id, ProviderType.BITBUCKET_DATA_CENTER
                )
            )
            logger.debug(
                f'Got Bitbucket DC token {bitbucket_dc_token} from user ID: {self.user_id}'
            )
        else:
            logger.warning('external_auth_token and user_id not set!')
        if bitbucket_dc_token:
            self.token = bitbucket_dc_token
        return bitbucket_dc_token
