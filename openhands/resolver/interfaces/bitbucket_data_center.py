import base64
from typing import Any

import httpx

from openhands.core.logger import openhands_logger as logger
from openhands.resolver.interfaces.issue import (
    Issue,
    IssueHandlerInterface,
    ReviewThread,
)
from openhands.resolver.utils import extract_issue_references


class BitbucketDataCenterPRHandler(IssueHandlerInterface):
    """Handler for Bitbucket Data Center pull requests.

    Uses Bitbucket Server REST API 1.0:
      https://{base_domain}/rest/api/1.0/projects/{owner}/repos/{repo}/...

    Authentication: Basic auth — token must be in username:password format.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        username: str | None = None,
        base_domain: str = '',
    ):
        self.owner = owner
        self.repo = repo
        self.token = token
        self.username = username

        domain = base_domain.strip()
        domain = domain.replace('https://', '').replace('http://', '').rstrip('/')
        self.base_domain = domain

        self.base_url = self.get_base_url()
        self.download_url = self.get_download_url()
        self.clone_url = self.get_clone_url()
        self.headers = self.get_headers()

    def set_owner(self, owner: str) -> None:
        self.owner = owner

    def get_headers(self) -> dict[str, str]:
        auth_str = base64.b64encode(self.token.encode()).decode()
        return {
            'Authorization': f'Basic {auth_str}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

    def get_base_url(self) -> str:
        return f'https://{self.base_domain}/rest/api/1.0'

    def _repo_api_base(self) -> str:
        return f'{self.base_url}/projects/{self.owner}/repos/{self.repo}'

    def get_download_url(self) -> str:
        return (
            f'https://{self.base_domain}/rest/api/latest'
            f'/projects/{self.owner}/repos/{self.repo}/archive?format=zip'
        )

    def get_clone_url(self) -> str:
        return f'https://{self.token}@{self.base_domain}/scm/{self.owner.lower()}/{self.repo}.git'

    def get_repo_url(self) -> str:
        return f'https://{self.base_domain}/projects/{self.owner}/repos/{self.repo}'

    def get_issue_url(self, issue_number: int) -> str:
        return f'{self.get_repo_url()}/pull-requests/{issue_number}'

    def get_pr_url(self, pr_number: int) -> str:
        return f'{self.get_repo_url()}/pull-requests/{pr_number}'

    def get_pull_url(self, pr_number: int) -> str:
        return self.get_pr_url(pr_number)

    def get_branch_url(self, branch_name: str) -> str:
        return f'{self.get_repo_url()}/browse?at=refs/heads/{branch_name}'

    def get_compare_url(self, branch_name: str) -> str:
        default = self.get_default_branch_name()
        return f'{self.get_repo_url()}/compare/diff?targetBranch=refs/heads/{default}&sourceBranch=refs/heads/{branch_name}'

    def get_authorize_url(self) -> str:
        return f'https://{self.token}@{self.base_domain}/'

    def get_graphql_url(self) -> str:
        # Bitbucket Data Center does not expose a GraphQL API.
        return ''

    def get_default_branch_name(self) -> str:
        url = f'{self._repo_api_base()}/default-branch'
        try:
            response = httpx.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return data.get('displayId', 'main')
        except Exception:
            logger.warning(
                f'Failed to get default branch for {self.owner}/{self.repo}, falling back to "main"',
                exc_info=True,
            )
            return 'main'

    def get_branch_name(self, base_branch_name: str) -> str:
        return f'{base_branch_name}-{self.owner.lower()}'

    def branch_exists(self, branch_name: str) -> bool:
        url = f'{self._repo_api_base()}/branches'
        try:
            response = httpx.get(
                url, headers=self.headers, params={'filterText': branch_name, 'limit': 1}
            )
            response.raise_for_status()
            data = response.json()
            return any(
                b.get('displayId') == branch_name
                for b in data.get('values', [])
            )
        except Exception:
            logger.warning(
                f'Failed to check branch existence for {branch_name!r} in {self.owner}/{self.repo}',
                exc_info=True,
            )
            return False

    async def get_issue(self, issue_number: int) -> Issue:
        """Fetch a pull request as an Issue object."""
        url = f'{self._repo_api_base()}/pull-requests/{issue_number}'
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

        return Issue(
            owner=self.owner,
            repo=self.repo,
            number=data.get('id', issue_number),
            title=data.get('title', ''),
            body=data.get('description', ''),
            head_branch=data.get('fromRef', {}).get('displayId'),
            base_branch=data.get('toRef', {}).get('displayId'),
        )

    def download_issues(self) -> list[Any]:
        """Fetch open pull requests for the repository."""
        url = f'{self._repo_api_base()}/pull-requests'
        pull_requests: list[Any] = []
        start = 0
        limit = 100

        while True:
            try:
                response = httpx.get(
                    url,
                    headers=self.headers,
                    params={'state': 'OPEN', 'start': start, 'limit': limit},
                )
                response.raise_for_status()
                data = response.json()
                pull_requests.extend(data.get('values', []))
                if data.get('isLastPage', True):
                    break
                start = data.get('nextPageStart', start + limit)
            except httpx.HTTPError as e:
                logger.warning(f'Failed to fetch pull requests: {e}')
                break

        return pull_requests

    def get_issue_comments(
        self, issue_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        url = f'{self._repo_api_base()}/pull-requests/{issue_number}/activities'
        comments: list[str] = []
        start = 0
        limit = 100

        while True:
            try:
                response = httpx.get(
                    url,
                    headers=self.headers,
                    params={'start': start, 'limit': limit},
                )
                response.raise_for_status()
                data = response.json()
                for activity in data.get('values', []):
                    if activity.get('action') == 'COMMENTED':
                        comment = activity.get('comment', {})
                        if comment_id is None or comment.get('id') == comment_id:
                            text = comment.get('text', '')
                            if text:
                                comments.append(text)
                if data.get('isLastPage', True):
                    break
                start = data.get('nextPageStart', start + limit)
            except httpx.HTTPError as e:
                logger.warning(f'Failed to fetch PR comments: {e}')
                break

        return comments

    def get_issue_thread_comments(self, issue_number: int) -> list[str]:
        return self.get_issue_comments(issue_number) or []

    def get_issue_review_comments(self, issue_number: int) -> list[str]:
        return []

    def get_issue_review_threads(self, issue_number: int) -> list[ReviewThread]:
        return []

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        if not issue_numbers:
            raise ValueError('Unspecified issue numbers')

        all_prs = self.download_issues()
        filtered = [pr for pr in all_prs if pr.get('id') in issue_numbers]

        converted: list[Issue] = []
        for pr in filtered:
            if not pr.get('id') or not pr.get('title'):
                logger.warning(f'Skipping PR {pr} as it is missing id or title.')
                continue

            converted.append(
                Issue(
                    owner=self.owner,
                    repo=self.repo,
                    number=pr['id'],
                    title=pr['title'],
                    body=pr.get('description', ''),
                    head_branch=pr.get('fromRef', {}).get('displayId'),
                    base_branch=pr.get('toRef', {}).get('displayId'),
                )
            )

        return converted

    def create_pull_request(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if data is None:
            data = {}

        url = f'{self._repo_api_base()}/pull-requests'
        source = data.get('source_branch', '')
        target = data.get('target_branch', '')
        repo_ref = {'slug': self.repo, 'project': {'key': self.owner}}

        payload = {
            'title': data.get('title', ''),
            'description': data.get('description', ''),
            'state': 'OPEN',
            'open': True,
            'closed': False,
            'fromRef': {'id': f'refs/heads/{source}', 'repository': repo_ref},
            'toRef': {'id': f'refs/heads/{target}', 'repository': repo_ref},
            'reviewers': [],
        }

        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()
        pr_data = response.json()

        links = pr_data.get('links', {}).get('self', [])
        html_url = links[0].get('href', '') if links else ''

        return {'html_url': html_url, 'number': pr_data.get('id', 0)}

    def reply_to_comment(self, pr_number: int, comment_id: str, reply: str) -> None:
        url = f'{self._repo_api_base()}/pull-requests/{pr_number}/comments/{comment_id}'
        payload = {'text': reply}
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()

    def send_comment_msg(self, issue_number: int, msg: str) -> None:
        url = f'{self._repo_api_base()}/pull-requests/{issue_number}/comments'
        payload = {'text': msg}
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()

    def request_reviewers(self, reviewer: str, pr_number: int) -> None:
        logger.warning('BitbucketDataCenterPRHandler.request_reviewers not implemented')

    def get_context_from_external_issues_references(
        self,
        closing_issues: list[str],
        closing_issue_numbers: list[int],
        issue_body: str,
        review_comments: list[str] | None,
        review_threads: list[ReviewThread],
        thread_comments: list[str] | None,
    ) -> list[str]:
        new_refs: list[int] = []

        for text in [issue_body] + (review_comments or []) + (thread_comments or []):
            if text:
                new_refs.extend(extract_issue_references(text))

        for thread in review_threads:
            new_refs.extend(extract_issue_references(thread.comment))

        for issue_number in set(new_refs) - set(closing_issue_numbers):
            url = f'{self._repo_api_base()}/pull-requests/{issue_number}'
            try:
                response = httpx.get(url, headers=self.headers)
                response.raise_for_status()
                body = response.json().get('description', '')
                if body:
                    closing_issues.append(body)
            except httpx.HTTPError as e:
                logger.warning(f'Failed to fetch PR {issue_number}: {e}')

        return closing_issues

    def get_issue_references(self, body: str) -> list[int]:
        return extract_issue_references(body)
