from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode
from dataclasses import dataclass
from types import MappingProxyType

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import delete, select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.integrations.provider import ProviderToken
from openhands.integrations.service_types import ProviderType
from openhands.storage.data_models.secrets import Secrets
from openhands.storage.secrets.secrets_store import SecretsStore

# Special key for storing provider tokens in custom_secrets table
PROVIDER_TOKENS_KEY = '__provider_tokens__'


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

            kwargs = {}
            provider_tokens = {}

            for secret in settings:
                if secret.secret_name == PROVIDER_TOKENS_KEY:
                    # Parse provider tokens from JSON
                    try:
                        tokens_json = json.loads(secret.secret_value)
                        for provider_type_str, token_data in tokens_json.items():
                            provider_type = ProviderType(provider_type_str)
                            provider_tokens[provider_type] = ProviderToken(
                                token=SecretStr(token_data['token']) if token_data.get('token') else None,
                                user_id=token_data.get('user_id'),
                                host=token_data.get('host'),
                            )
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.error(f'Error parsing provider tokens: {e}')
                else:
                    kwargs[secret.secret_name] = {
                        'secret': secret.secret_value,
                        'description': secret.description,
                    }

            self._decrypt_kwargs(kwargs)

            return Secrets(
                custom_secrets=kwargs,  # type: ignore[arg-type]
                provider_tokens=MappingProxyType(provider_tokens) if provider_tokens else MappingProxyType({}),
            )

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

            # Extract provider_tokens before encryption
            provider_tokens_data = kwargs.pop('provider_tokens', {})

            self._encrypt_kwargs(kwargs)

            secrets_json = kwargs.get('custom_secrets', {})

            # Extract the secrets into tuples for insertion or updating
            secret_tuples = []
            for secret_name, secret_info in secrets_json.items():
                secret_value = secret_info.get('secret')
                description = secret_info.get('description')

                secret_tuples.append((secret_name, secret_value, description))

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

            # Store provider_tokens as JSON if present
            if provider_tokens_data:
                tokens_to_store = {}
                for provider_type, token_data in provider_tokens_data.items():
                    provider_type_str = provider_type.value if hasattr(provider_type, 'value') else str(provider_type)
                    tokens_to_store[provider_type_str] = {
                        'token': token_data.get('token'),
                        'user_id': token_data.get('user_id'),
                        'host': token_data.get('host'),
                    }

                provider_tokens_json = json.dumps(tokens_to_store)
                new_secret = StoredCustomSecrets(
                    keycloak_user_id=self.user_id,
                    org_id=org_id,
                    secret_name=PROVIDER_TOKENS_KEY,
                    secret_value=provider_tokens_json,
                    description='Git provider tokens',
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
