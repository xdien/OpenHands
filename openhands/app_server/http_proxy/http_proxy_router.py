"""HTTP Proxy Router for sandbox services.

This module provides HTTP proxy functionality to forward requests from the frontend
to sandbox services (VSCode, web apps, etc.) running on dynamic ports.

The proxy maps paths like /proxy/{port}/... to http://localhost:{port}/...
This allows the frontend to access sandbox services through the backend proxy
instead of directly connecting to random ports.
"""

import logging
import os
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


async def get_sandbox_url_for_port(sandbox_port: int) -> str | None:
    """Look up the sandbox URL from the database based on port.

    Returns the internal_url from the sandbox's exposed_urls if found.
    Currently returns None to use fallback hosts - can be enhanced later
    to query the database when proper dependency injection is available.
    """
    return None


async def http_proxy_to_sandbox(request: Request) -> Response:
    """HTTP proxy handler - forwards requests to sandbox services.

    This function handles HTTP requests to /proxy/{sandbox_port}/... paths
    and forwards them to the appropriate sandbox service port.

    Args:
        request: The incoming HTTP request (Starlette Request)

    Returns:
        The proxied response from the sandbox service
    """
    # Extract path parameters from the URL
    # Path format: /proxy/{sandbox_port}/...
    path = request.url.path

    # Parse the port from the path
    # /proxy/{port}/remaining/path -> extract port
    parts = path.split('/')
    if len(parts) < 3:
        return Response(
            status_code=400, content='Invalid proxy path format. Use /proxy/{port}/...'
        )

    try:
        sandbox_port = int(parts[2])
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port in proxy path: {path} - {e}')
        return Response(
            status_code=400,
            content=f'Invalid port: {parts[2] if len(parts) > 2 else "missing"}',
        )

    # Build the remaining path after /proxy/{port}/
    remaining_path = '/'.join(parts[3:]) if len(parts) > 3 else ''
    if remaining_path:
        # Add leading slash if not present
        remaining_path = '/' + remaining_path

    # Build query string
    query_string = str(request.url.query) if request.url.query else ''

    logger.info(f'HTTP PROXY: /proxy/{sandbox_port}/{remaining_path}')

    # Try to get the actual sandbox URL from the database
    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    # Fallback: try different hosts
    hosts_to_try: list[str] = []
    if sandbox_base_url:
        # Extract host from database URL
        try:
            if '://' in sandbox_base_url:
                hosts_to_try.append(sandbox_base_url.split('://')[1].split('/')[0])
        except Exception:
            pass

    # Add fallback hosts for Docker/remote scenarios
    hosts_to_try.extend(
        [
            f'localhost:{sandbox_port}',
            f'127.0.0.1:{sandbox_port}',
        ]
    )

    # In Docker, try host.docker.internal
    if os.path.exists('/.dockerenv'):
        hosts_to_try.append(f'host.docker.internal:{sandbox_port}')

    # Deduplicate
    hosts_to_try = list(dict.fromkeys(hosts_to_try))

    # Build the target URL
    target_url = None
    for host in hosts_to_try:
        target_url = f'http://{host}{remaining_path}'
        if query_string:
            target_url += f'?{query_string}'

        logger.info(f'Trying HTTP proxy to: {target_url.split("?")[0]}')

        try:
            # Forward the request to the sandbox
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Copy headers but remove host
                headers = dict(request.headers)
                headers.pop('host', None)
                headers.pop('Host', None)

                # Add forwarded headers to help the app understand its actual URL
                headers['X-Forwarded-Host'] = (
                    f'{request.url.hostname}:{request.url.port}'
                )
                headers['X-Forwarded-Proto'] = request.url.scheme
                headers['X-Forwarded-Prefix'] = f'/proxy/{sandbox_port}'
                headers['X-Real-IP'] = (
                    request.client.host if request.client else 'unknown'
                )

                # Log headers for debugging redirect loops
                logger.info(
                    f'Proxy headers for {sandbox_port}: X-Forwarded-Prefix=/proxy/{sandbox_port}'
                )

                # Get request body
                body = await request.body()

                # Make the request
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                )

                logger.info(
                    f'HTTP proxy success: {host}, status={response.status_code}'
                )

                # Handle redirect responses - rewrite Location header to preserve /proxy/{port}/ prefix
                response_headers = dict(response.headers)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response_headers.get(
                        'location', response_headers.get('Location')
                    )
                    logger.info(
                        f'Redirect detected: status={response.status_code}, location={location}, request_path={request.url.path}'
                    )
                    if location:
                        # Rewrite relative redirects to include /proxy/{sandbox_port}/ prefix
                        if location.startswith('/') and not location.startswith(
                            f'/proxy/{sandbox_port}'
                        ):
                            new_location = f'/proxy/{sandbox_port}{location}'
                            # Preserve critical query parameters (tkn) from original request
                            # Parse both URLs to merge query params intelligently

                            # Parse the redirect location
                            parsed_redirect = urlparse(new_location)
                            redirect_params = parse_qs(
                                parsed_redirect.query, keep_blank_values=True
                            )

                            # Parse original request query
                            original_query = request.url.query
                            original_params = parse_qs(
                                original_query, keep_blank_values=True
                            )

                            # Merge: only add params from original that aren't in redirect
                            # Special focus on 'tkn' which is critical for VSCode auth
                            for key, values in original_params.items():
                                if key not in redirect_params:
                                    redirect_params[key] = values

                            # Rebuild the URL with merged params
                            merged_query = urlencode(redirect_params, doseq=True)
                            new_location = urlunparse(
                                (
                                    parsed_redirect.scheme,
                                    parsed_redirect.netloc,
                                    parsed_redirect.path,
                                    parsed_redirect.params,
                                    merged_query,
                                    parsed_redirect.fragment,
                                )
                            )
                            response_headers['location'] = new_location
                            response_headers['Location'] = new_location
                            logger.info(
                                f'Rewritten redirect Location: {location} -> {new_location}'
                            )
                        else:
                            logger.info(f'No rewrite needed for location: {location}')

                # Remove X-Frame-Options header to allow embedding in iframe
                # This is necessary because OpenHands displays apps in iframes
                xfo_headers = ['x-frame-options', 'X-Frame-Options']
                for xfo in xfo_headers:
                    if xfo in response_headers:
                        logger.info(f'Removing {xfo} header to allow iframe embedding')
                        del response_headers[xfo]

                # Also remove Content-Security-Policy frame-ancestors restrictions
                csp_headers = ['content-security-policy', 'Content-Security-Policy']
                for csp in csp_headers:
                    if csp in response_headers:
                        csp_value = response_headers[csp]
                        # Remove frame-ancestors directive if present
                        if 'frame-ancestors' in csp_value.lower():
                            logger.info(
                                f'Modifying {csp} header to remove frame-ancestors restriction'
                            )
                            # Split directives and filter out frame-ancestors
                            directives = [d.strip() for d in csp_value.split(';')]
                            filtered = [
                                d
                                for d in directives
                                if not d.lower().startswith('frame-ancestors')
                            ]
                            if filtered:
                                response_headers[csp] = '; '.join(filtered)
                            else:
                                del response_headers[csp]

                # Return the response with proper content
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.ConnectError as e:
            logger.warning(f'Failed to connect to {host}: {e}')
            continue
        except Exception as e:
            logger.warning(f'Error proxying to {host}: {e}')
            continue

    # If we get here, all hosts failed
    logger.error(
        f'Could not proxy to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
    )
    return Response(
        status_code=503, content=f'Service not available on port {sandbox_port}'
    )


async def http_ws_proxy_to_sandbox(request: Request) -> Response:
    """HTTP proxy handler for /ws/{sandbox_port}/api/* paths.

    This function handles HTTP requests to /ws/{sandbox_port}/api/... paths
    and forwards them to the appropriate sandbox service port.
    This provides an alternative URL pattern to /proxy/{sandbox_port}/api/...

    Supports multiple port ranges:
    - Agent Server: 30000-39999
    - VSCode: 40000-49999
    - App/Browser 1: 50000-54999
    - App/Browser 2: 55000-59999
    - Remote: 60000-60002

    Args:
        request: The incoming HTTP request (Starlette Request)

    Returns:
        The proxied response from the sandbox service
    """
    # Extract path parameters from the URL
    # Path format: /ws/{sandbox_port}/api/...
    path = request.url.path

    # Parse the port from the path
    # /ws/{port}/api/remaining/path -> extract port
    parts = path.split('/')
    if len(parts) < 4:
        return Response(
            status_code=400,
            content='Invalid proxy path format. Use /ws/{sandbox_port}/api/...',
        )

    try:
        sandbox_port = int(parts[2])
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port in proxy path: {path} - {e}')
        return Response(
            status_code=400,
            content=f'Invalid port: {parts[2] if len(parts) > 2 else "missing"}',
        )

    # Build the remaining path after /ws/{port}/
    # Keep the /api/ prefix as part of the remaining path
    remaining_path = '/'.join(parts[3:]) if len(parts) > 3 else ''
    if remaining_path:
        # Add leading slash if not present
        remaining_path = '/' + remaining_path

    # Build query string
    query_string = str(request.url.query) if request.url.query else ''

    logger.info(f'HTTP WS PROXY: /ws/{sandbox_port}{remaining_path}')

    # Try to get the actual sandbox URL from the database
    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    # Fallback: try different hosts
    hosts_to_try: list[str] = []
    if sandbox_base_url:
        # Extract host from database URL
        try:
            if '://' in sandbox_base_url:
                hosts_to_try.append(sandbox_base_url.split('://')[1].split('/')[0])
        except Exception:
            pass

    # Add fallback hosts for Docker/remote scenarios
    hosts_to_try.extend(
        [
            f'localhost:{sandbox_port}',
            f'127.0.0.1:{sandbox_port}',
        ]
    )

    # In Docker, try host.docker.internal
    if os.path.exists('/.dockerenv'):
        hosts_to_try.append(f'host.docker.internal:{sandbox_port}')

    # Deduplicate
    hosts_to_try = list(dict.fromkeys(hosts_to_try))

    # Build the target URL
    target_url = None
    for host in hosts_to_try:
        target_url = f'http://{host}{remaining_path}'
        if query_string:
            target_url += f'?{query_string}'

        logger.info(f'Trying HTTP WS proxy to: {target_url.split("?")[0]}')

        try:
            # Forward the request to the sandbox
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Copy headers but remove host
                headers = dict(request.headers)
                headers.pop('host', None)
                headers.pop('Host', None)

                # Get request body
                body = await request.body()

                # Make the request
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                )

                logger.info(f'HTTP WS proxy success: {host}')

                # Return the response with proper content
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )

        except httpx.ConnectError as e:
            logger.warning(f'Failed to connect to {host}: {e}')
            continue
        except Exception as e:
            logger.warning(f'Error proxying to {host}: {e}')
            continue

    # If we get here, all hosts failed
    logger.error(
        f'Could not proxy to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
    )
    return Response(
        status_code=503, content=f'Service not available on port {sandbox_port}'
    )


async def http_ws_root_proxy_to_sandbox(request: Request) -> Response:
    """HTTP proxy handler for /ws/{sandbox_port}/ root path.

    This function handles HTTP requests to /ws/{sandbox_port}/ paths
    (with trailing slash but no additional path).
    Used for VSCode and other sandbox services accessed via root URL.

    Args:
        request: The incoming HTTP request (Starlette Request)

    Returns:
        The proxied response from the sandbox service
    """
    # Extract path parameters from the URL
    # Path format: /ws/{sandbox_port}/
    path = request.url.path

    # Parse the port from the path
    parts = path.split('/')
    if len(parts) < 3:
        return Response(
            status_code=400,
            content='Invalid proxy path format. Use /ws/{sandbox_port}/',
        )

    try:
        sandbox_port = int(parts[2])
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port in proxy path: {path} - {e}')
        return Response(
            status_code=400,
            content=f'Invalid port: {parts[2] if len(parts) > 2 else "missing"}',
        )

    # Build the remaining path (root path = /)
    remaining_path = '/'

    # Build query string
    query_string = str(request.url.query) if request.url.query else ''
    # Try to get the actual sandbox URL from the database
    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    # Fallback: try different hosts
    hosts_to_try: list[str] = []
    if sandbox_base_url:
        # Extract host from database URL
        try:
            if '://' in sandbox_base_url:
                hosts_to_try.append(sandbox_base_url.split('://')[1].split('/')[0])
        except Exception:
            pass

    # Add fallback hosts for Docker/remote scenarios
    hosts_to_try.extend(
        [
            f'localhost:{sandbox_port}',
            f'127.0.0.1:{sandbox_port}',
        ]
    )

    # In Docker, try host.docker.internal
    if os.path.exists('/.dockerenv'):
        hosts_to_try.append(f'host.docker.internal:{sandbox_port}')

    # Deduplicate
    hosts_to_try = list(dict.fromkeys(hosts_to_try))

    # Build the target URL
    target_url = None
    for host in hosts_to_try:
        target_url = f'http://{host}{remaining_path}'
        if query_string:
            target_url += f'?{query_string}'

        logger.info(f'Trying HTTP WS ROOT proxy to: {target_url.split("?")[0]}')

        try:
            # Forward the request to the sandbox
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Copy headers but remove host
                headers = dict(request.headers)
                headers.pop('host', None)
                headers.pop('Host', None)

                # Get request body
                body = await request.body()

                # Make the request
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                )

                logger.info(f'HTTP WS ROOT proxy success: {host}')

                # Handle redirect responses - rewrite Location header to preserve /ws/{port}/ prefix
                response_headers = dict(response.headers)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response_headers.get(
                        'location', response_headers.get('Location')
                    )
                    if location:
                        # Rewrite relative redirects to include /ws/{sandbox_port}/ prefix
                        if location.startswith('/') and not location.startswith(
                            f'/ws/{sandbox_port}'
                        ):
                            new_location = f'/ws/{sandbox_port}{location}'
                            response_headers['location'] = new_location
                            response_headers['Location'] = new_location
                            logger.info(
                                f'Rewritten redirect Location: {location} -> {new_location}'
                            )

                # Return the response with proper content
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.ConnectError as e:
            logger.warning(f'Failed to connect to {host}: {e}')
            continue
        except Exception as e:
            logger.warning(f'Error proxying to {host}: {e}')
            continue

    # If we get here, all hosts failed
    logger.error(
        f'Could not proxy to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
    )
    return Response(
        status_code=503, content=f'Service not available on port {sandbox_port}'
    )
