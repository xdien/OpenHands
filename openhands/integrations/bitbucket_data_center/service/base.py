import base64
from typing import Any

import httpx
from pydantic import SecretStr

from openhands.core.logger import openhands_logger as logger
from openhands.integrations.protocols.http_client import HTTPClient
from openhands.integrations.service_types import (
    BaseGitService,
    OwnerType,
    ProviderType,
    Repository,
    RequestMethod,
    ResourceNotFoundError,
    User,
)
from openhands.utils.http_session import httpx_verify_option


class BitbucketDCMixinBase(BaseGitService, HTTPClient):
    """
    Base mixin for Bitbucket Data Center service containing common functionality.
    Uses Bitbucket Server REST API 1.0 at https://{domain}/rest/api/1.0
    """

    BASE_URL: str = ''  # Set dynamically from domain in __init__
    user_id: str | None = None

    def _extract_owner_and_repo(self, repository: str) -> tuple[str, str]:
        """Extract project key and repo slug from repository string.

        Args:
            repository: Repository name in format 'PROJECT/repo_slug'

        Returns:
            Tuple of (project_key, repo_slug)

        Raises:
            ValueError: If repository format is invalid
        """
        parts = repository.split('/')
        if len(parts) < 2:
            raise ValueError(f'Invalid repository name: {repository}')
        return parts[-2], parts[-1]

    async def get_latest_token(self) -> SecretStr | None:
        """Get latest working token of the user."""
        return self.token

    async def _get_headers(self) -> dict[str, str]:
        """Get headers for Bitbucket Data Center API requests."""
        token_value = self.token.get_secret_value()

        if ':' in token_value:
            auth_str = base64.b64encode(token_value.encode()).decode()
            return {
                'Authorization': f'Basic {auth_str}',
                'Accept': 'application/json',
            }
        else:
            return {
                'Authorization': f'Bearer {token_value}',
                'Accept': 'application/json',
            }

    async def _make_request(
        self,
        url: str,
        params: dict | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[Any, dict]:
        """Make a request to the Bitbucket Data Center API."""
        try:
            async with httpx.AsyncClient(verify=httpx_verify_option()) as client:
                headers = await self._get_headers()
                response = await self.execute_request(
                    client, url, headers, params, method
                )
                if self.refresh and self._has_token_expired(response.status_code):
                    await self.get_latest_token()
                    headers = await self._get_headers()
                    response = await self.execute_request(
                        client=client,
                        url=url,
                        headers=headers,
                        params=params,
                        method=method,
                    )
                response.raise_for_status()
                return response.json(), dict(response.headers)
        except httpx.HTTPStatusError as e:
            raise self.handle_http_status_error(e)
        except httpx.HTTPError as e:
            raise self.handle_http_error(e)

    async def _fetch_paginated_data(
        self, url: str, params: dict, max_items: int
    ) -> list[dict]:
        """Fetch data with pagination support for Bitbucket Server API 1.0.

        Bitbucket Server uses isLastPage + nextPageStart instead of a 'next' URL.
        """
        all_items: list[dict] = []
        current_params = dict(params)

        while len(all_items) < max_items:
            response, _ = await self._make_request(url, current_params)

            page_items = response.get('values', [])
            all_items.extend(page_items)

            if response.get('isLastPage', True):
                break

            next_page_start = response.get('nextPageStart')
            if next_page_start is None:
                break

            current_params = dict(params)
            current_params['start'] = next_page_start

        return all_items[:max_items]

    async def verify_access(self) -> None:
        """Verify that the token and host are valid by making a lightweight API call.

        Raises an exception if the token is invalid or the host is unreachable.
        """
        url = f'{self.BASE_URL}/repos'
        await self._make_request(url, {'limit': '1'})

    async def get_user(self) -> User:
        """Get the authenticated user's information."""
        if not self.user_id:
            # PAT-only auth with no username — return a minimal user object.
            return User(
                id='',
                login='',
                avatar_url='',
                name=None,
                email=None,
            )

        url = f'{self.BASE_URL}/users'
        try:
            data, _ = await self._make_request(url, {'filter': self.user_id})
            users = data.get('values', [])
            if users:
                user = users[0]
                return User(
                    id=str(user.get('id', '')),
                    login=user.get('slug', ''),
                    avatar_url='',
                    name=user.get('displayName'),
                    email=user.get('emailAddress'),
                )
        except Exception:
            logger.warning(
                'bitbucket_data_center:get_user:lookup_failed',
                exc_info=True,
            )

        # Return minimal user if lookup fails
        return User(
            id=self.user_id,
            login=self.user_id,
            avatar_url='',
            name=None,
            email=None,
        )

    def _parse_repository(
        self, repo: dict, link_header: str | None = None
    ) -> Repository:
        """Parse a Bitbucket Data Center API repository response into a Repository object."""
        project_key = repo.get('project', {}).get('key', '')
        repo_slug = repo.get('slug', '')

        if not project_key or not repo_slug:
            raise ValueError(
                f'Cannot parse repository: missing project key or slug. '
                f'Got project_key={project_key!r}, repo_slug={repo_slug!r}'
            )

        full_name = f'{project_key}/{repo_slug}'
        is_public = repo.get('public', False)

        # defaultBranch is a plain string in the DC REST API schema
        main_branch: str | None = repo.get('defaultBranch') or None

        return Repository(
            id=str(repo.get('id', '')),
            full_name=full_name,
            git_provider=ProviderType.BITBUCKET_DATA_CENTER,
            is_public=is_public,
            stargazers_count=None,
            pushed_at=None,
            owner_type=OwnerType.ORGANIZATION,
            link_header=link_header,
            main_branch=main_branch,
        )

    async def get_repository_details_from_repo_name(
        self, repository: str
    ) -> Repository:
        """Get repository details from repository name.

        Args:
            repository: Repository name in format 'PROJECT/repo_slug'

        Returns:
            Repository object with details
        """
        project, repo_slug = self._extract_owner_and_repo(repository)
        url = f'{self.BASE_URL}/projects/{project}/repos/{repo_slug}'
        data, _ = await self._make_request(url)
        return self._parse_repository(data)

    async def _get_cursorrules_url(self, repository: str) -> str:
        """Get the URL for fetching .cursorrules file.

        Uses the /browse/ endpoint (returns JSON) rather than /raw/ (returns
        plain text), because _make_request always calls response.json().
        """
        repo_details = await self.get_repository_details_from_repo_name(repository)
        if not repo_details.main_branch:
            raise ResourceNotFoundError(
                f'Main branch not found for repository {repository}. '
                f'This repository may be empty or have no default branch configured.'
            )
        project, repo_slug = self._extract_owner_and_repo(repository)
        return (
            f'{self.BASE_URL}/projects/{project}/repos/{repo_slug}/browse/.cursorrules'
            f'?at=refs/heads/{repo_details.main_branch}'
        )

    async def _get_microagents_directory_url(
        self, repository: str, microagents_path: str
    ) -> str:
        """Get the URL for checking microagents directory."""
        repo_details = await self.get_repository_details_from_repo_name(repository)
        if not repo_details.main_branch:
            raise ResourceNotFoundError(
                f'Main branch not found for repository {repository}. '
                f'This repository may be empty or have no default branch configured.'
            )
        project, repo_slug = self._extract_owner_and_repo(repository)
        return (
            f'{self.BASE_URL}/projects/{project}/repos/{repo_slug}/browse/{microagents_path}'
            f'?at=refs/heads/{repo_details.main_branch}'
        )

    def _get_microagents_directory_params(self, microagents_path: str) -> dict | None:
        """Get parameters for the microagents directory request."""
        return None

    def _is_valid_microagent_file(self, item: dict) -> bool:
        """Check if an item represents a valid microagent file."""
        file_name = item.get('path', {}).get('name', '')
        return (
            item.get('type') == 'FILE'
            and file_name.endswith('.md')
            and file_name != 'README.md'
        )

    def _get_file_name_from_item(self, item: dict) -> str:
        """Extract file name from directory item."""
        return item.get('path', {}).get('name', '')

    def _get_file_path_from_item(self, item: dict, microagents_path: str) -> str:
        """Extract file path from directory item."""
        return item.get('path', {}).get('toString', '')

    async def _process_microagents_directory(
        self, repository: str, microagents_path: str
    ) -> list:
        """Override for Bitbucket DC browse endpoint directory structure.

        The DC browse endpoint returns directory contents nested under
        response['children']['values'], not at the top-level response['values']
        that the base implementation expects.
        """
        from openhands.microagent.types import MicroagentResponse

        microagents: list[MicroagentResponse] = []

        try:
            directory_url = await self._get_microagents_directory_url(
                repository, microagents_path
            )

            # DC browse paginates via isLastPage / nextPageStart
            start = 0
            while True:
                params = {'start': start} if start > 0 else None
                response, _ = await self._make_request(directory_url, params)

                # DC browse for a directory: {"children": {"values": [...], ...}, ...}
                children = response.get('children', {})
                items = children.get('values', [])

                for item in items:
                    if self._is_valid_microagent_file(item):
                        try:
                            file_name = self._get_file_name_from_item(item)
                            file_path = self._get_file_path_from_item(
                                item, microagents_path
                            )
                            microagents.append(
                                self._create_microagent_response(file_name, file_path)
                            )
                        except Exception as e:
                            logger.warning(
                                f'Error processing microagent {item.get("path", {}).get("name", "unknown")}: {e}'
                            )

                if children.get('isLastPage', True):
                    break
                start = children.get('nextPageStart', start + 25)
        except ResourceNotFoundError:
            logger.info(
                f'No microagents directory found in {repository} at {microagents_path}'
            )

        return microagents
