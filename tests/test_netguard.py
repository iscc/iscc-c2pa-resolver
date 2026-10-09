"""Tests for the outbound connection guard against server-side request forgery."""

import httpcore2
import httpx2
import pytest

from iscc_c2pa_resolver.netguard import (
    BlockedAddressError,
    PublicOnlyBackend,
    is_public,
    public_transport,
    resolve_public,
)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", True),
        ("2001:4860:4860::8888", True),
        ("::ffff:8.8.8.8", True),
        ("127.0.0.1", False),
        ("0.0.0.0", False),  # noqa: S104
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata endpoint
        ("100.64.0.1", False),  # carrier-grade NAT
        ("224.0.0.1", False),  # multicast
        ("::1", False),
        ("fc00::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("::ffff:10.0.0.1", False),
    ],
)
def test_is_public(address, expected):
    assert is_public(address) is expected


async def test_resolve_public_accepts_public_address():
    assert await resolve_public("8.8.8.8", 443) == "8.8.8.8"


async def test_resolve_public_blocks_localhost():
    with pytest.raises(BlockedAddressError, match="non-public"):
        await resolve_public("localhost", 443)


@pytest.mark.parametrize("host", ["does-not-exist.invalid", "example..com", "a" * 64 + ".com"])
async def test_resolve_public_blocks_unresolvable_name(host):
    with pytest.raises(BlockedAddressError, match="Cannot resolve"):
        await resolve_public(host, 443)


class RecordingBackend(httpcore2.AsyncMockBackend):
    """Mock backend that records the hosts it is asked to connect to."""

    def __init__(self):
        super().__init__(buffer=[])
        self.hosts = []  # type: list[str]

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


async def test_backend_connects_to_checked_address():
    inner = RecordingBackend()
    backend = PublicOnlyBackend(inner)
    await backend.connect_tcp("8.8.8.8", 443)
    assert inner.hosts == ["8.8.8.8"]


async def test_backend_refuses_private_host_before_connecting():
    inner = RecordingBackend()
    with pytest.raises(BlockedAddressError):
        await PublicOnlyBackend(inner).connect_tcp("127.0.0.1", 443)
    assert inner.hosts == []


async def test_backend_refuses_unix_sockets():
    with pytest.raises(BlockedAddressError, match="Unix"):
        await PublicOnlyBackend().connect_unix_socket("/var/run/docker.sock")


async def test_backend_sleep_delegates():
    await PublicOnlyBackend(RecordingBackend()).sleep(0)


@pytest.mark.parametrize("url", ["https://localhost:9/", "https://127.0.0.1:9/", "https://[::1]:9/"])
async def test_public_transport_blocks_loopback(url):
    async with httpx2.AsyncClient(transport=public_transport()) as client:
        with pytest.raises(httpx2.ConnectError) as info:
            await client.get(url)
    assert isinstance(info.value.__cause__, BlockedAddressError)
