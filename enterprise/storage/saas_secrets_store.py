from __future__ import annotations

import hashlib
from base64 import b64decode, b64encode
from dataclasses import dataclass

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.secrets.secrets_store import SecretsStore


@dataclass
class SaasSecretsStore(SecretsStore):
    user_id: str
    config: OpenHandsConfig

    async def load(self) -> Secrets | None:
        if not self.user_id:
            return None
        user = await UserStore.get_user_by_id(self.user_id)
        org_id = user.current_org_id if user else None

        async with a_session_maker() as session:
            # Fetch all secrets for the given user ID
            query = select(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id
            )
            if org_id is not None:
                query = query.filter(StoredCustomSecrets.org_id == org_id)
            result = await session.execute(query)
            settings = result.scalars().all()

            if not settings:
                return Secrets()

            custom_secrets = {}
            provider_tokens = {}
            for secret in settings:
                secret_data = {
                    'secret': secret.secret_value,
                    'description': secret.description,
                }
                # Check if this is a provider token (stored with special prefix)
                if secret.secret_name.startswith('provider_tokens:'):
                    provider_type = secret.secret_name.replace('provider_tokens:', '')
                    provider_tokens[provider_type] = secret_data
                else:
                    custom_secrets[secret.secret_name] = secret_data

            self._decrypt_kwargs(custom_secrets)
            self._decrypt_kwargs(provider_tokens)

            # Convert to ProviderToken objects
            from types import MappingProxyType

            from pydantic import SecretStr

            from openhands.storage.data_models.secrets import ProviderToken

            provider_tokens_result = {}
            for provider_type, token_data in provider_tokens.items():
                if token_data.get('secret'):  # Only include if token exists
                    provider_tokens_result[provider_type] = ProviderToken(
                        token=SecretStr(token_data['secret']),
                        user_id=None,
                        host=None,
                    )

            return Secrets(
                custom_secrets=custom_secrets,
                provider_tokens=MappingProxyType(provider_tokens_result),
            )  # type: ignore[arg-type]

    async def store(self, item: Secrets):
        user = await UserStore.get_user_by_id(self.user_id)
        if user is None:
            raise ValueError(f'User not found: {self.user_id}')
        org_id = user.current_org_id

        async with a_session_maker() as session:
            # Incoming secrets are always the most updated ones
            # Delete existing records for this user AND organization only
            delete_query = delete(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id
            )
            if org_id is not None:
                delete_query = delete_query.filter(StoredCustomSecrets.org_id == org_id)
            else:
                delete_query = delete_query.filter(StoredCustomSecrets.org_id.is_(None))
            await session.execute(delete_query)

            # Prepare the new secrets data
            kwargs = item.model_dump(context={'expose_secrets': True})
            # Keep provider_tokens in kwargs - they need to be stored too
            # (previously incorrectly deleted - this was a bug)
            self._encrypt_kwargs(kwargs)

            secrets_json = kwargs.get('custom_secrets', {})
            provider_tokens_json = kwargs.get('provider_tokens', {})

            # Extract the secrets into tuples for insertion or updating
            secret_tuples = []
            for secret_name, secret_info in secrets_json.items():
                secret_value = secret_info.get('secret')
                description = secret_info.get('description')

                secret_tuples.append((secret_name, secret_value, description))

            # Also store provider_tokens with special prefix
            for provider_type, token_info in provider_tokens_json.items():
                # token_info can be a string (when serialized with expose_secrets=True)
                # or a dict (when serialized without expose_secrets)
                if isinstance(token_info, str):
                    secret_value = token_info
                elif isinstance(token_info, dict):
                    secret_value = token_info.get('token')
                else:
                    secret_value = None
                if secret_value:  # Only store if token exists
                    secret_tuples.append(
                        (f'provider_tokens:{provider_type}', secret_value, None)
                    )

            # Add the new secrets
            for secret_name, secret_value, description in secret_tuples:
                new_secret = StoredCustomSecrets(
                    keycloak_user_id=self.user_id,
                    org_id=org_id,
                    secret_name=secret_name,
                    secret_value=secret_value,
                    description=description,
                )
                session.add(new_secret)

            await session.commit()

    def _decrypt_kwargs(self, kwargs: dict):
        fernet = self._fernet()
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._decrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                value = fernet.decrypt(b64decode(value.encode())).decode()
                kwargs[key] = value

    def _encrypt_kwargs(self, kwargs: dict):
        fernet = self._fernet()
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._encrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                encrypted_value = b64encode(fernet.encrypt(value.encode())).decode()
                kwargs[key] = encrypted_value

    def _fernet(self):
        if not self.config.jwt_secret:
            raise Exception('config.jwt_secret must be set')
        jwt_secret = self.config.jwt_secret.get_secret_value()
        fernet_key = b64encode(hashlib.sha256(jwt_secret.encode()).digest())
        return Fernet(fernet_key)

    @classmethod
    async def get_instance(
        cls,
        config: OpenHandsConfig,
        user_id: str,  # type: ignore[override]
    ) -> SaasSecretsStore:
        logger.debug(f'saas_secrets_store.get_instance::{user_id}')
        return SaasSecretsStore(user_id, config)
