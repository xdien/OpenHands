"""HTTP Proxy Router for sandbox services.

This module provides HTTP proxy functionality to forward requests from the frontend
to sandbox services (VSCode, web apps, etc.) running on dynamic ports.

URL Patterns:
- /proxy/{port}/... - Generic HTTP proxy for any sandbox service
- /vscode/{port}/... - VSCode-specific proxy with cookie-based auth handling
- /worker1/{port}/... - Worker1 proxy (for multi-container sandboxes)
- /worker2/{port}/... - Worker2 proxy (for multi-container sandboxes)
- /ws/{port}/... - WebSocket proxy

The proxy maps paths to http://localhost:{port}/...
This allows the frontend to access sandbox services through the backend proxy
instead of directly connecting to random ports.
"""

import logging
import os
from urllib.parse import parse_qs, urlencode

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

    # Extract tkn from query string and pass via header for services that need header auth
    # For VSCode ports (40000-49999), we also need to extract token for redirect handling
    token = None
    is_vscode_port = 40000 <= sandbox_port <= 49999

    if query_string:
        parsed = parse_qs(query_string, keep_blank_values=True)
        if 'tkn' in parsed:
            token = parsed['tkn'][0]
            # Remove tkn from query string to avoid duplication (only for non-VSCode)
            # VSCode ports need token in query string for the initial request
            if not is_vscode_port:
                filtered_params = {k: v for k, v in parsed.items() if k != 'tkn'}
                if filtered_params:
                    query_string = urlencode(filtered_params, doseq=True)
                else:
                    query_string = ''
            logger.info(
                f'Extracted tkn from query for auth, is_vscode={is_vscode_port}'
            )

    # For VSCode ports, if no token in query, try to get from X-Session-API-Key header
    # This handles cases where frontend passes token via header instead of query
    if is_vscode_port and not token:
        header_token = request.headers.get('X-Session-API-Key')
        if header_token:
            token = header_token
            logger.info('Extracted tkn from X-Session-API-Key header for VSCode')

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

                # Pass token via header for VSCode authentication (survives redirects)
                if token:
                    headers['X-Session-API-Key'] = token
                    logger.info(
                        f'Added X-Session-API-Key header for port {sandbox_port}'
                    )

                # Log headers for debugging redirect loops
                logger.info(
                    f'Proxy headers for {sandbox_port}: X-Forwarded-Prefix=/proxy/{sandbox_port}, has_token=({token is not None})'
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
                        needs_rewrite = location.startswith(
                            '/'
                        ) and not location.startswith(f'/proxy/{sandbox_port}')

                        is_vscode_port = 40000 <= sandbox_port <= 49999

                        if needs_rewrite:
                            location = f'/proxy/{sandbox_port}{location}'
                            logger.info(
                                f'Added /proxy/{sandbox_port} prefix to redirect'
                            )

                        # For VSCode ports, don't add token to redirect
                        # The initial request from browser has token, subsequent requests use cookie
                        if not is_vscode_port and token:
                            # Update location header
                            if location.startswith('/'):
                                response_headers['location'] = location
                                response_headers['Location'] = location
                                logger.info(f'Rewritten redirect Location: {location}')
                            else:
                                logger.info(
                                    f'No rewrite needed for location: {location}'
                                )

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


async def http_vscode_proxy_to_sandbox(request: Request) -> Response:
    """HTTP proxy handler for VSCode-specific paths.

    This function handles HTTP requests to /vscode/{sandbox_port}/... paths
    and forwards them to VSCode server with special handling for:
    - Cookie-based authentication (vscode-tkn cookie)
    - Redirect handling without adding token to URL
    - Proper header forwarding for VSCode WebSocket connections

    Path format: /vscode/{sandbox_port}/...

    Args:
        request: The incoming HTTP request (Starlette Request)

    Returns:
        The proxied response from the VSCode server
    """
    # Extract path parameters from the URL
    # Path format: /vscode/{sandbox_port}/...
    path = request.url.path

    # Parse the port from the path
    parts = path.split('/')
    if len(parts) < 3:
        return Response(
            status_code=400,
            content='Invalid VSCode proxy path format. Use /vscode/{port}/...',
        )

    try:
        sandbox_port = int(parts[2])
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port in VSCode proxy path: {path} - {e}')
        return Response(
            status_code=400,
            content=f'Invalid port: {parts[2] if len(parts) > 2 else "missing"}',
        )

    # Build the remaining path after /vscode/{port}/
    remaining_path = '/'.join(parts[3:]) if len(parts) > 3 else ''
    if remaining_path:
        remaining_path = '/' + remaining_path

    # Build query string
    query_string = str(request.url.query) if request.url.query else ''

    # Extract token from query string for VSCode initial auth
    # VSCode uses cookie-based auth after first request
    token = None
    if query_string:
        parsed = parse_qs(query_string, keep_blank_values=True)
        if 'tkn' in parsed:
            token = parsed['tkn'][0]
            logger.info(
                f'VSCode proxy: extracted tkn from query for port {sandbox_port}'
            )

    logger.info(f'VSCode PROXY: /vscode/{sandbox_port}{remaining_path}')

    # Try to get the actual sandbox URL from the database
    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    # Fallback: try different hosts
    hosts_to_try: list[str] = []
    if sandbox_base_url:
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
    for host in hosts_to_try:
        target_url = f'http://{host}{remaining_path}'
        if query_string:
            target_url += f'?{query_string}'

        logger.info(f'Trying VSCode proxy to: {target_url.split("?")[0]}')

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Copy headers but remove host
                headers = dict(request.headers)
                headers.pop('host', None)
                headers.pop('Host', None)

                # Add forwarded headers
                headers['X-Forwarded-Host'] = (
                    f'{request.url.hostname}:{request.url.port}'
                )
                headers['X-Forwarded-Proto'] = request.url.scheme
                headers['X-Forwarded-Prefix'] = f'/vscode/{sandbox_port}'
                headers['X-Real-IP'] = (
                    request.client.host if request.client else 'unknown'
                )

                # Pass token via header for authentication
                if token:
                    headers['X-Session-API-Key'] = token

                logger.info(
                    f'VSCode proxy headers for {sandbox_port}: has_token=({token is not None})'
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
                    f'VSCode proxy success: {host}, status={response.status_code}'
                )

                # Handle redirect responses - rewrite Location header
                response_headers = dict(response.headers)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response_headers.get(
                        'location', response_headers.get('Location')
                    )
                    logger.info(
                        f'VSCode redirect detected: status={response.status_code}, location={location}'
                    )
                    if location:
                        # Rewrite relative redirects to include /vscode/{sandbox_port}/ prefix
                        if location.startswith('/') and not location.startswith(
                            f'/vscode/{sandbox_port}'
                        ):
                            location = f'/vscode/{sandbox_port}{location}'
                            logger.info(
                                f'VSCode: Added /vscode/{sandbox_port} prefix to redirect'
                            )

                        # IMPORTANT: Don't add token to redirect for VSCode
                        # VSCode uses cookie-based auth after first request
                        # Browser will automatically send the vscode-tkn cookie

                        # Update location header
                        if location.startswith('/'):
                            response_headers['location'] = location
                            response_headers['Location'] = location
                            logger.info(
                                f'VSCode rewritten redirect Location: {location}'
                            )

                # Remove X-Frame-Options header to allow embedding in iframe
                xfo_headers = ['x-frame-options', 'X-Frame-Options']
                for xfo in xfo_headers:
                    if xfo in response_headers:
                        logger.info(f'VSCode: Removing {xfo} header')
                        del response_headers[xfo]

                # Remove Content-Security-Policy entirely for VSCode
                # VSCode sends invalid CSP headers with domain:port which causes browser errors
                csp_headers = ['content-security-policy', 'Content-Security-Policy']
                for csp in csp_headers:
                    if csp in response_headers:
                        logger.info(
                            f'VSCode: Removing {csp} header to avoid browser CSP errors'
                        )
                        del response_headers[csp]

                # Return the response
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.ConnectError as e:
            logger.warning(f'VSCode: Failed to connect to {host}: {e}')
            continue
        except Exception as e:
            logger.warning(f'VSCode: Error proxying to {host}: {e}')
            continue

    # If we get here, all hosts failed
    logger.error(
        f'VSCode: Could not proxy to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
    )
    return Response(
        status_code=503, content=f'VSCode service not available on port {sandbox_port}'
    )


async def http_worker1_proxy_to_sandbox(request: Request) -> Response:
    """HTTP proxy handler for Worker1-specific paths.

    This function handles HTTP requests to /worker1/{sandbox_port}/... paths
    and forwards them to Worker1 containers in multi-container sandboxes.
    Used for additional containers like WORKER_1, WORKER_2 in the sandbox.

    Path format: /worker1/{sandbox_port}/...

    Args:
        request: The incoming HTTP request (Starlette Request)

    Returns:
        The proxied response from the Worker1 container
    """
    path = request.url.path

    parts = path.split('/')
    if len(parts) < 3:
        return Response(
            status_code=400,
            content='Invalid Worker1 proxy path format. Use /worker1/{port}/...',
        )

    try:
        sandbox_port = int(parts[2])
        if not (30000 <= sandbox_port <= 65535):
            raise ValueError(f'Port {sandbox_port} out of range')
    except (ValueError, TypeError) as e:
        logger.error(f'Invalid port in Worker1 proxy path: {path} - {e}')
        return Response(
            status_code=400,
            content=f'Invalid port: {parts[2] if len(parts) > 2 else "missing"}',
        )

    remaining_path = '/'.join(parts[3:]) if len(parts) > 3 else ''
    if remaining_path:
        remaining_path = '/' + remaining_path

    query_string = str(request.url.query) if request.url.query else ''

    token = None
    if query_string:
        parsed = parse_qs(query_string, keep_blank_values=True)
        if 'tkn' in parsed:
            token = parsed['tkn'][0]
            filtered_params = {k: v for k, v in parsed.items() if k != 'tkn'}
            if filtered_params:
                query_string = urlencode(filtered_params, doseq=True)
            else:
                query_string = ''

    logger.info(f'Worker1 PROXY: {path}')

    sandbox_base_url = await get_sandbox_url_for_port(sandbox_port)

    hosts_to_try: list[str] = []
    if sandbox_base_url:
        try:
            if '://' in sandbox_base_url:
                hosts_to_try.append(sandbox_base_url.split('://')[1].split('/')[0])
        except Exception:
            pass

    hosts_to_try.extend(
        [
            f'localhost:{sandbox_port}',
            f'127.0.0.1:{sandbox_port}',
        ]
    )

    if os.path.exists('/.dockerenv'):
        hosts_to_try.append(f'host.docker.internal:{sandbox_port}')

    hosts_to_try.append(f'172.17.0.1:{sandbox_port}')

    hosts_to_try = list(dict.fromkeys(hosts_to_try))

    for host in hosts_to_try:
        target_url = f'http://{host}{remaining_path}'
        if query_string:
            target_url += f'?{query_string}'

        logger.info(f'Trying Worker1 proxy to: {target_url.split("?")[0]}')

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = dict(request.headers)
                headers.pop('host', None)
                headers.pop('Host', None)

                headers['X-Forwarded-Host'] = (
                    f'{request.url.hostname}:{request.url.port}'
                )
                headers['X-Forwarded-Proto'] = request.url.scheme
                headers['X-Forwarded-Prefix'] = f'/worker1/{sandbox_port}'
                headers['X-Real-IP'] = (
                    request.client.host if request.client else 'unknown'
                )

                if token:
                    headers['X-Session-API-Key'] = token

                body = await request.body()

                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                )

                logger.info(
                    f'Worker1 proxy success: {host}, status={response.status_code}'
                )

                response_headers = dict(response.headers)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response_headers.get(
                        'location', response_headers.get('Location')
                    )
                    if location:
                        if location.startswith('/') and not location.startswith(
                            f'/worker1/{sandbox_port}'
                        ):
                            location = f'/worker1/{sandbox_port}{location}'
                            logger.info(
                                f'Worker1: Added /worker1/{sandbox_port} prefix to redirect'
                            )

                        if location.startswith('/'):
                            response_headers['location'] = location
                            response_headers['Location'] = location

                xfo_headers = ['x-frame-options', 'X-Frame-Options']
                for xfo in xfo_headers:
                    if xfo in response_headers:
                        del response_headers[xfo]

                csp_headers = ['content-security-policy', 'Content-Security-Policy']
                for csp in csp_headers:
                    if csp in response_headers:
                        csp_value = response_headers[csp]
                        if 'frame-ancestors' in csp_value.lower():
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

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=response_headers,
                )

        except httpx.ConnectError as e:
            logger.warning(f'Worker1: Failed to connect to {host}: {e}')
            continue
        except Exception as e:
            logger.warning(f'Worker1: Error proxying to {host}: {e}')
            continue

    logger.error(
        f'Worker1: Could not proxy to sandbox at port {sandbox_port} after trying: {hosts_to_try}'
    )
    return Response(
        status_code=503, content=f'Worker1 service not available on port {sandbox_port}'
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
