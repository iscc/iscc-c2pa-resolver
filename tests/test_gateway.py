"""Tests for reading manifest addresses from gateway URLs, probing them, and fetching manifests."""

import asyncio

import httpx2
import pytest

from iscc_c2pa_resolver.gateway import (
    HIT_TTL,
    MAX_MANIFEST_BYTES,
    MISS_TTL,
    PROBE_BYTES,
    RETRY_TTL,
    ManifestUnavailable,
    Pointer,
    expires_at,
    fetch_manifest,
    is_https_url,
    is_manifest_id,
    is_manifest_store,
    new_cache,
    new_client,
    parse_pointer,
    probe_manifest,
)
from iscc_c2pa_resolver.netguard import public_transport
from tests.conftest import MANIFEST, MANIFEST_ID, MANIFEST_URL, TITUSZ_GATEWAY_URL

POINTER = Pointer(manifest_id=MANIFEST_ID, manifest_url=MANIFEST_URL)
# The real manifest address of a testnet asset, keyed by the repository's own ID instead of the C2PA URN.
SOUNDMARQUE = "https://www.soundmarque.com/api/c2pa/v1/manifests/b5118aa8-4f5c-45ca-882d-2b7805b3bc83"
# A IIIF Presentation manifest: a common JSON document that also lives under a /manifests/ path.
IIIF = {"@context": "http://iiif.io/api/presentation/3/context.json", "id": MANIFEST_URL, "type": "Manifest"}


def test_parse_pointer():
    assert parse_pointer(MANIFEST_URL) == POINTER


def test_parse_pointer_accepts_repository_keys():
    assert parse_pointer(SOUNDMARQUE) == Pointer(
        manifest_id="b5118aa8-4f5c-45ca-882d-2b7805b3bc83", manifest_url=SOUNDMARQUE
    )


@pytest.mark.parametrize(
    ("url", "manifest_id"),
    [
        ("https://repo.example/v1/manifests/urn%3Ac2pa%3AF9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4", MANIFEST_ID),
        (f"https://repo.example/v1/manifests/{MANIFEST_ID}:acme/x", f"{MANIFEST_ID}:acme/x"),  # "/" in the URN
        (f"https://repo.example/v1/manifests/{MANIFEST_ID}%3Aacme%2Fx", f"{MANIFEST_ID}:acme/x"),
        ("https://repo.example/manifests/api/v1/manifests/abc", "abc"),  # the last route segment counts
        ("https://repo.example/v1/manifests/abc?returnActiveManifest=true", "abc"),  # the query is not part of it
    ],
)
def test_parse_pointer_reads_manifest_id(url, manifest_id):
    assert parse_pointer(url) == Pointer(manifest_id=manifest_id, manifest_url=url)


@pytest.mark.parametrize(
    "url",
    [
        TITUSZ_GATEWAY_URL,  # an ordinary gateway document
        "https://repo.example/v1/manifests/",  # no manifest ID
        "https://repo.example/v1/manifest/abc",
        "https://repo.example/v1?next=/manifests/abc",  # the route only in the query
        "http://repo.example/v1/manifests/abc",
        "https://repo.example:invalid/v1/manifests/abc",
        "https://repo.example/v1/manifests/a%20b",
        "https://repo.example/v1/manifests/a%00b",
        "https://repo.example/v1/manifests/" + "a" * 257,
    ],
)
def test_parse_pointer_rejects(url):
    assert parse_pointer(url) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (MANIFEST_URL, True),
        ("http://repo.example/m", False),
        ("https://", False),
        ("https://[::1", False),  # invalid URL
        ("https://example.com:invalid/", False),  # invalid port
        ("https://repo.example/\ud800", False),  # lone surrogate
        ("https://repo.example/a b", False),
        ("https://repo.example/" + "a" * 2048, False),
        (None, False),
    ],
)
def test_is_https_url(value, expected):
    assert is_https_url(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (MANIFEST_ID, True),
        ("b5118aa8-4f5c-45ca-882d-2b7805b3bc83", True),
        ("contentauth:urn:uuid:b2b1f7fa-b119-4de1-9c0d-c97fbea3f2c3", True),
        ("", False),
        ("a" * 257, False),
        ("urn:c2pa:\x00", False),
        ("urn c2pa", False),
    ],
)
def test_is_manifest_id(value, expected):
    assert is_manifest_id(value) is expected


def test_is_manifest_store():
    assert is_manifest_store(MANIFEST)
    assert is_manifest_store(MANIFEST[:PROBE_BYTES])
    assert not is_manifest_store(b"")
    assert not is_manifest_store(MANIFEST[:31])
    assert not is_manifest_store(MANIFEST[:16] + bytes(16) + MANIFEST[32:])  # JUMBF, but not a manifest store


def client_for(handler):
    # type: (object) -> httpx2.AsyncClient
    """A gateway client whose requests go to a handler function."""
    return new_client(httpx2.MockTransport(handler), timeout=1.0, user_agent="test")  # type: ignore[arg-type]


async def probe(handler, url=MANIFEST_URL, timeout=1.0):
    # type: (object, str, float) -> tuple[bool, int]
    """Run probe_manifest against a handler function."""
    async with client_for(handler) as client:
        return await probe_manifest(client, url, timeout)


async def test_probe_manifest_hit():
    seen = []
    assert await probe(lambda request: seen.append(request) or httpx2.Response(200, content=MANIFEST)) == (
        True,
        HIT_TTL,
    )
    assert seen[0].headers["accept"] == "application/c2pa"
    assert seen[0].headers["user-agent"] == "test"


async def test_probe_manifest_reads_first_bytes_only():
    streamed = []

    async def body():
        for chunk in (MANIFEST, b"x" * MAX_MANIFEST_BYTES):
            streamed.append(len(chunk))
            yield chunk

    assert await probe(lambda request: httpx2.Response(200, content=body())) == (True, HIT_TTL)
    assert streamed == [len(MANIFEST)]


async def test_probe_manifest_reads_across_chunks():
    async def body():
        for i in range(PROBE_BYTES):
            yield MANIFEST[i : i + 1]

    assert await probe(lambda request: httpx2.Response(200, content=body())) == (True, HIT_TTL)


async def test_probe_manifest_real_gateway_document(titusz_gateway):
    assert await probe(lambda request: httpx2.Response(200, json=titusz_gateway)) == (False, MISS_TTL)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json=IIIF),
        httpx2.Response(200, text="<html>"),
        httpx2.Response(200, content=MANIFEST[:31]),
        httpx2.Response(200),
        httpx2.Response(404),
        httpx2.Response(410),
        httpx2.Response(302, headers={"Location": "http://internal.example/"}),
    ],
)
async def test_probe_manifest_miss(response):
    assert await probe(lambda request: response) == (False, MISS_TTL)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_probe_manifest_repository_failing(status):
    assert await probe(lambda request: httpx2.Response(status)) == (False, RETRY_TTL)


async def test_probe_manifest_follows_https_redirect():
    def handler(request):
        if request.url.path == "/moved":
            return httpx2.Response(200, content=MANIFEST)
        return httpx2.Response(301, headers={"Location": "/moved"})

    assert await probe(handler) == (True, HIT_TTL)


async def test_probe_manifest_does_not_read_redirect_bodies():
    streamed = []

    async def padding():
        for _ in range(32):
            streamed.append(1)
            yield b"x" * 1024

    def handler(request):
        if request.url.path == "/moved":
            return httpx2.Response(200, content=MANIFEST)
        return httpx2.Response(302, headers={"Location": "/moved"}, content=padding())

    assert await probe(handler) == (True, HIT_TTL)
    assert streamed == []


async def test_probe_manifest_limits_redirects():
    def handler(request):
        return httpx2.Response(302, headers={"Location": f"/next{len(request.url.path)}"})

    assert await probe(handler) == (False, RETRY_TTL)


async def test_probe_manifest_network_error():
    def handler(request):
        raise httpx2.ConnectError("refused")

    assert await probe(handler) == (False, RETRY_TTL)


async def test_probe_manifest_malformed_host_name():
    async with new_client(public_transport(), timeout=1.0, user_agent="test") as client:
        assert await probe_manifest(client, "https://example..com/v1/manifests/a", 1.0) == (False, RETRY_TTL)


async def test_probe_manifest_time_budget():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx2.Response(200, content=MANIFEST)

    assert await probe(handler, timeout=0.01) == (False, RETRY_TTL)


async def manifest(handler, timeout=1.0):
    # type: (object, float) -> bytes | None
    """Run fetch_manifest against a handler function."""
    async with client_for(handler) as client:
        return await fetch_manifest(client, MANIFEST_URL, timeout)


async def test_fetch_manifest():
    seen = []
    assert await manifest(lambda request: seen.append(request) or httpx2.Response(200, content=MANIFEST)) == MANIFEST
    assert seen[0].headers["accept"] == "application/c2pa"


async def test_fetch_manifest_follows_redirect():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/moved":
            return httpx2.Response(200, content=MANIFEST)
        return httpx2.Response(302, headers={"Location": "/moved"})

    assert await manifest(handler, timeout=5.0) == MANIFEST
    assert [r.extensions["timeout"]["read"] for r in seen] == [5.0, 5.0]  # its own budget, not the client's 1.0


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(404),
        httpx2.Response(200, text="<html>"),
        httpx2.Response(200, content=MANIFEST + b"x" * MAX_MANIFEST_BYTES),
        httpx2.Response(302, headers={"Location": "http://internal.example/"}),
    ],
)
async def test_fetch_manifest_not_served(response):
    assert await manifest(lambda request: response) is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_fetch_manifest_repository_failing(status):
    with pytest.raises(ManifestUnavailable):
        await manifest(lambda request: httpx2.Response(status))


async def test_fetch_manifest_network_error():
    def handler(request):
        raise httpx2.ConnectError("refused")

    with pytest.raises(ManifestUnavailable):
        await manifest(handler)


async def test_fetch_manifest_time_budget():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx2.Response(200, content=MANIFEST)

    with pytest.raises(ManifestUnavailable):
        await manifest(handler, timeout=0.01)


async def test_client_keeps_no_cookies():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/moved":
            return httpx2.Response(200, content=MANIFEST, headers={"Set-Cookie": "b=2; Path=/"})
        return httpx2.Response(302, headers={"Location": "/moved", "Set-Cookie": "a=1; Path=/"})

    async with client_for(handler) as client:
        await fetch_manifest(client, MANIFEST_URL, 1.0)
        await fetch_manifest(client, MANIFEST_URL, 1.0)
        assert len(client.cookies.jar) == 0
    assert [r.headers.get("cookie") for r in seen] == [None] * 4


def test_cache_entries_carry_their_lifetime():
    assert expires_at(MANIFEST_URL, (False, MISS_TTL), 100.0) == 100.0 + MISS_TTL
    cache = new_cache(maxsize=2)
    cache[MANIFEST_URL] = (True, HIT_TTL)
    assert cache[MANIFEST_URL] == (True, HIT_TTL)
