from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select
from storage.database import a_session_maker
from storage.stored_custom_secrets import StoredCustomSecrets
from storage.user_store import UserStore

from openhands.app_server.secrets.secrets_models import Secrets
from openhands.app_server.secrets.secrets_store import SecretsStore
from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.logger import openhands_logger as logger

# Special key for storing provider tokens in custom_secrets table
PROVIDER_TOKENS_KEY = '__provider_tokens__'


@dataclass
class SaasSecretsStore(SecretsStore):
    user_id: str
    _jwt_svc: JwtService = field(repr=False)
    # When set, overrides the user's `current_org_id` for both load and
    # store. Used to honor a request's effective org (api_key_org_id >
    # X-Org-Id header > user.current_org_id). Secrets are stored per
    # (user_id, org_id), so the effective org must flow through here for
    # the right rows to be read/written.
    effective_org_id: UUID | None = None

    async def load(self) -> Secrets | None:
        if not self.user_id:
            return None
        user = await UserStore.get_user_by_id(self.user_id)
        org_id = self.effective_org_id or (user.current_org_id if user else None)

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
        org_id = self.effective_org_id or user.current_org_id

        async with a_session_maker() as session:
            # Incoming secrets are always the most updated ones
            # Delete existing records for this user AND organization only
            # org_id is always set: it's either the effective org from
            # the request or the user's non-nullable current_org_id.
            delete_query = delete(StoredCustomSecrets).filter(
                StoredCustomSecrets.keycloak_user_id == self.user_id,
                StoredCustomSecrets.org_id == org_id,
            )
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
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._decrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                kwargs[key] = self._jwt_svc.decrypt_value(value)

    def _encrypt_kwargs(self, kwargs: dict):
        for key, value in kwargs.items():
            if isinstance(value, dict):
                self._encrypt_kwargs(value)
                continue

            if value is None:
                kwargs[key] = value
            else:
                kwargs[key] = self._jwt_svc.encrypt_value(value)

    @classmethod
    async def get_instance(  # type: ignore[override]
        cls,
        user_id: str,
        effective_org_id: UUID | None = None,
    ) -> SaasSecretsStore:
        """Get a SaasSecretsStore instance for the given user.

        Args:
            user_id: Keycloak user id.
            effective_org_id: Optional org id resolved from the request
                (see SaasUserAuth.get_effective_org_id). When None the
                store falls back to ``user.current_org_id`` to preserve
                legacy behavior for background / non-request callers
                (e.g. webhook resolvers).

        TODO: This method should be replaced with dependency injection.
        """
        logger.debug(f'saas_secrets_store.get_instance::{user_id}')
        from storage.encrypt_utils import get_jwt_service

        return SaasSecretsStore(
            user_id,
            get_jwt_service(),
            effective_org_id=effective_org_id,
        )
