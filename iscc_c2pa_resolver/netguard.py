"""Outbound HTTP that connects to public internet addresses only (protection against server-side request forgery).

Gateway URLs come from anyone who declares an ISCC. Before each TCP connection the host name is resolved, every
resolved address must be globally routable, and the socket connects to the checked address itself, so a second DNS
lookup cannot swap in a private one. TLS still verifies the certificate against the original host name.
"""

import asyncio
import ipaddress
import socket
import typing

import httpcore2
import httpx2


class BlockedAddressError(httpcore2.ConnectError):
    """Raised when a host resolves to an address outside the public internet."""


def is_public(address):
    # type: (str) -> bool
    """Tell whether an IP address is globally routable unicast, unwrapping IPv4-mapped IPv6."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public(host, port):
    # type: (str, int) -> str
    """Resolve a host name and return its first address if all of its addresses are public.

    :raises BlockedAddressError: if any resolved address is not public, or the name does not resolve.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:  # UnicodeError for names that are not valid IDNA, e.g. "example..com"
        raise BlockedAddressError(f"Cannot resolve {host}") from e
    addresses = [str(info[4][0]) for info in infos]
    if not addresses or not all(is_public(address) for address in addresses):
        raise BlockedAddressError(f"{host} resolves to a non-public address")
    return addresses[0]


class PublicOnlyBackend(httpcore2.AsyncNetworkBackend):
    """Network backend that refuses TCP connections to non-public addresses."""

    def __init__(self, inner=None):
        # type: (httpcore2.AsyncNetworkBackend | None) -> None
        # httpcore2.AnyIOBackend is typed as a fallback stub; anyio is always installed with httpx2
        self._inner = inner or typing.cast(httpcore2.AsyncNetworkBackend, httpcore2.AnyIOBackend())

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # type: (str, int, float | None, str | None, object) -> httpcore2.AsyncNetworkStream
        """Connect to the checked public address of `host`."""
        address = await resolve_public(host, port)
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,  # type: ignore[arg-type]
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        # type: (str, float | None, object) -> httpcore2.AsyncNetworkStream
        """Refuse Unix sockets."""
        raise BlockedAddressError("Unix sockets are not allowed")

    async def sleep(self, seconds):
        # type: (float) -> None
        """Delegate sleeping to the wrapped backend."""
        await self._inner.sleep(seconds)


def public_transport(max_connections=32):
    # type: (int) -> httpx2.AsyncHTTPTransport
    """Build an httpx2 transport whose connections go to public addresses only.

    httpx2 has no public hook for the network backend, so the connection pool is replaced after construction.
    `test_public_transport_blocks_loopback` guards this against httpx2 changes.
    """
    transport = httpx2.AsyncHTTPTransport(trust_env=False)
    transport._pool = httpcore2.AsyncConnectionPool(
        ssl_context=httpx2.create_ssl_context(trust_env=False),
        max_connections=max_connections,
        max_keepalive_connections=max_connections // 2,
        keepalive_expiry=30.0,
        network_backend=PublicOnlyBackend(),
    )
    return transport
