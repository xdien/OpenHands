from urllib.parse import urlparse

from openhands.integrations.bitbucket_data_center.service.base import (
    BitbucketDCMixinBase,
)
from openhands.integrations.service_types import (
    AuthenticationError,
    Repository,
    ResourceNotFoundError,
    SuggestedTask,
    UnknownException,
)
from openhands.server.types import AppMode


class BitbucketDCReposMixin(BitbucketDCMixinBase):
    """
    Mixin for Bitbucket Data Center repository-related operations.
    Uses /projects/{key}/repos endpoints from REST API 1.0.
    """

    async def get_installations(
        self, query: str | None = None, limit: int = 100
    ) -> list[str]:
        """Get all accessible project keys."""
        url = f'{self.BASE_URL}/projects'
        params: dict = {'limit': limit}
        if query:
            params['name'] = query

        projects = await self._fetch_paginated_data(url, params, limit)
        keys = [p['key'] for p in projects if 'key' in p]
        return keys

    async def get_paginated_repos(
        self,
        page: int,
        per_page: int,
        sort: str,
        installation_id: str | None,
        query: str | None = None,
    ) -> list[Repository]:
        """Get paginated repositories for a specific project.

        Args:
            page: Page number (1-based; converted to start offset for Server API)
            per_page: Number of repositories per page
            sort: Sort field (unused; Server API doesn't support sort on repos)
            installation_id: The project key to fetch repositories from

        Returns:
            A list of Repository objects
        """
        if not installation_id:
            return []

        project_key = installation_id
        url = f'{self.BASE_URL}/projects/{project_key}/repos'

        start = (page - 1) * per_page
        params: dict = {'start': start, 'limit': per_page}
        if query:
            params['name'] = query

        response, _ = await self._make_request(url, params)

        repos = response.get('values', [])
        has_next = not response.get('isLastPage', True)

        link_header = ''
        if has_next:
            next_start = response.get('nextPageStart', start + per_page)
            link_header = f'<{url}?start={next_start}&limit={per_page}>; rel="next"'

        return [
            self._parse_repository(repo, link_header=link_header) for repo in repos
        ]

    async def get_all_repositories(
        self, sort: str, app_mode: AppMode
    ) -> list[Repository]:
        """Get all repositories across all accessible projects."""
        MAX_REPOS = 1000
        PER_PAGE = 100
        repositories: list[Repository] = []

        project_keys = await self.get_installations(limit=MAX_REPOS)

        for project_key in project_keys:
            if len(repositories) >= MAX_REPOS:
                break

            url = f'{self.BASE_URL}/projects/{project_key}/repos'
            params = {'limit': PER_PAGE}

            project_repos = await self._fetch_paginated_data(
                url, params, MAX_REPOS - len(repositories)
            )

            for repo in project_repos:
                repositories.append(self._parse_repository(repo))
                if len(repositories) >= MAX_REPOS:
                    break

        return repositories

    async def search_repositories(
        self,
        query: str,
        per_page: int,
        sort: str,
        order: str,
        public: bool,
        app_mode: AppMode,
    ) -> list[Repository]:
        """Search for repositories."""
        repositories: list[Repository] = []

        if public:
            # Parse URL to extract project/repo
            # Supports both:
            #   https://host/projects/PROJ/repos/REPO
            #   https://host/scm/proj/repo.git
            try:
                parsed = urlparse(query)
                path = parsed.path.rstrip('/')

                if '.git' in path:
                    # /scm/proj/repo.git  -> project=PROJ (upper), repo=repo
                    path = path.replace('.git', '')
                    segments = [s for s in path.split('/') if s]
                    # Remove leading 'scm' segment if present
                    if segments and segments[0].lower() == 'scm':
                        segments = segments[1:]
                    if len(segments) >= 2:
                        project_key = segments[0].upper()
                        repo_slug = segments[1]
                        repo = await self.get_repository_details_from_repo_name(
                            f'{project_key}/{repo_slug}'
                        )
                        repositories.append(repo)
                else:
                    # /projects/PROJ/repos/REPO
                    segments = [s for s in path.split('/') if s]
                    try:
                        proj_idx = segments.index('projects')
                        repos_idx = segments.index('repos')
                        if repos_idx > proj_idx:
                            project_key = segments[proj_idx + 1]
                            repo_slug = segments[repos_idx + 1]
                            repo = await self.get_repository_details_from_repo_name(
                                f'{project_key}/{repo_slug}'
                            )
                            repositories.append(repo)
                    except (ValueError, IndexError):
                        pass
            except Exception:
                pass
            return repositories

        # PROJECT/repo query
        if '/' in query:
            project_key, repo_query = query.split('/', 1)
            return await self.get_paginated_repos(
                1, per_page, sort, project_key.upper(), repo_query
            )

        # Plain text: search by repo name across all projects
        url = f'{self.BASE_URL}/repos'
        params = {'name': query, 'limit': per_page}
        try:
            response, _ = await self._make_request(url, params)
            for repo_data in response.get('values', []):
                repositories.append(self._parse_repository(repo_data))
        except AuthenticationError:
            raise
        except (ResourceNotFoundError, UnknownException):
            # Fallback: search project keys and repos under matching projects
            all_projects = await self.get_installations()
            matching_projects = [p for p in all_projects if query.upper() in p]
            for project_key in matching_projects:
                try:
                    repos = await self.get_paginated_repos(
                        1, per_page, sort, project_key
                    )
                    repositories.extend(repos)
                except Exception:
                    continue

        return repositories

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """Get suggested tasks for the authenticated user."""
        return []
