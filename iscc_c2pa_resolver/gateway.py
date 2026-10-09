"""Reading C2PA manifest addresses from the gateway URLs of declarations, and fetching the manifests.

A declaration takes part in C2PA resolution when its gateway URL is the manifest address of a C2PA Soft Binding
Resolution API, `https://{host}/{api}/manifests/{manifestId}`, and serves a C2PA Manifest Store. The manifest ID is
read from the URL. Whether the URL serves a manifest store is checked from its first bytes; the answer is cached per
URL by the caller. Gateway URLs of any other shape are never fetched.
"""

import asyncio
import logging
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import unquote, urlsplit

import httpx2
from cachetools import TLRUCache

log = logging.getLogger(__name__)

MANIFEST_ROUTE = "/manifests/"
PROBE_BYTES = 32  # enough to recognize a C2PA Manifest Store
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 2
MAX_URL_LENGTH = 2048
MAX_MANIFEST_ID_LENGTH = 256
HIT_TTL = 86400  # gateway URL serves a manifest store
MISS_TTL = 3600  # gateway URL answered with something else, or is gone
RETRY_TTL = 60  # gateway URL unreachable, timed out or failing
MANIFEST_ACCEPT = "application/c2pa"
MANIFEST_STORE_TYPE = bytes.fromhex("6332706100110010800000aa00389b71")  # JUMBF type of a C2PA Manifest Store


@dataclass(frozen=True)
class Pointer:
    """Where the C2PA Manifest Store for a manifest ID can be fetched."""

    manifest_id: str
    manifest_url: str


class GatewayRejected(Exception):
    """Raised for gateway or manifest responses the resolver refuses to read."""


class ManifestUnavailable(Exception):
    """Raised when a manifest repository cannot answer right now."""


def is_https_url(value):
    # type: (object) -> bool
    """Tell whether a value is an absolute HTTPS URL of acceptable length."""
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH or any(c.isspace() for c in value):
        return False
    try:
        url = httpx2.URL(value)
    except (httpx2.InvalidURL, UnicodeError):  # UnicodeError for lone surrogates
        return False
    return url.scheme == "https" and bool(url.host)


def is_manifest_id(value):
    # type: (str) -> bool
    """Tell whether a string can serve as a manifest ID: short, printable and without whitespace.

    C2PA recommends the URN of the active manifest (`urn:c2pa:...`); repositories may use their own identifiers.
    """
    return 0 < len(value) <= MAX_MANIFEST_ID_LENGTH and value.isprintable() and not any(c.isspace() for c in value)


def is_manifest_store(data):
    # type: (bytes) -> bool
    """Tell whether bytes start like a C2PA Manifest Store: a JUMBF superbox of the C2PA Manifest Store type."""
    return data[4:8] == b"jumb" and data[12:16] == b"jumd" and data[16:32] == MANIFEST_STORE_TYPE


def parse_pointer(url):
    # type: (str) -> Pointer | None
    """Read the manifest pointer from a gateway URL of the form `https://{host}/{api}/manifests/{manifestId}`.

    The manifest ID is the percent-decoded path after the last `/manifests/`; it may contain `/`. None for any other
    URL. A pointer is a candidate only, until its URL is found to serve a manifest store.
    """
    if not is_https_url(url):
        return None
    _, route, rest = urlsplit(url).path.rpartition(MANIFEST_ROUTE)
    manifest_id = unquote(rest)
    if not route or not is_manifest_id(manifest_id):
        return None
    return Pointer(manifest_id=manifest_id, manifest_url=url)


async def require_https(request):
    # type: (httpx2.Request) -> None
    """Request hook that refuses plain HTTP, also for redirect targets."""
    if request.url.scheme != "https":
        raise GatewayRejected(f"Refusing non-HTTPS gateway URL {request.url}")


async def read_body(response, limit, truncate):
    # type: (httpx2.Response, int, bool) -> bytes
    """Read at most `limit` bytes of a response body; a longer body is cut off if `truncate`, else refused."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if truncate and len(body) >= limit:
            return bytes(body[:limit])
        if len(body) > limit:
            raise GatewayRejected(f"Response exceeds {limit} bytes: {response.url}")
    return bytes(body)


async def read_capped(client, url, limit, timeout, truncate=False):
    # type: (httpx2.AsyncClient, str, int, float, bool) -> tuple[int, bytes]
    """GET a URL as `application/c2pa`, following at most MAX_REDIRECTS redirects; return the final status and body.

    Redirects are followed here rather than by the client, which would buffer redirect bodies without a limit. Only
    the body of a final 200 response is read, as `read_body` does. `timeout` applies to each network operation.
    """
    request = client.build_request("GET", url, headers={"Accept": MANIFEST_ACCEPT}, timeout=timeout)
    for _ in range(MAX_REDIRECTS + 1):
        response = await client.send(request, stream=True)
        try:
            if response.next_request is not None:
                request = response.next_request
                continue
            if response.status_code != 200:
                return response.status_code, b""
            return 200, await read_body(response, limit, truncate)
        finally:
            await response.aclose()
    raise httpx2.TooManyRedirects(f"Exceeded {MAX_REDIRECTS} redirects", request=request)


async def probe_manifest(client, url, timeout):
    # type: (httpx2.AsyncClient, str, float) -> tuple[bool, int]
    """Tell whether a URL serves a C2PA Manifest Store, judged from its first bytes, and how long to cache the answer.

    Reading stops after PROBE_BYTES, so a probe costs one short request even for a large manifest.
    """
    try:
        async with asyncio.timeout(timeout):
            status, head = await read_capped(client, url, PROBE_BYTES, timeout, truncate=True)
    except GatewayRejected as e:
        log.info("Gateway rejected: %s", e)
        return False, MISS_TTL
    except (httpx2.HTTPError, TimeoutError) as e:
        log.info("Gateway unreachable: %s (%s)", url, type(e).__name__)
        return False, RETRY_TTL
    if status == 429 or status >= 500:
        return False, RETRY_TTL
    found = status == 200 and is_manifest_store(head)
    return found, HIT_TTL if found else MISS_TTL


async def fetch_manifest(client, url, timeout):
    # type: (httpx2.AsyncClient, str, float) -> bytes | None
    """Fetch the C2PA Manifest Store at a manifest address; None if the address does not serve one.

    :raises ManifestUnavailable: on network errors, timeouts, rate limits and server errors.
    """
    try:
        async with asyncio.timeout(timeout):
            status, body = await read_capped(client, url, MAX_MANIFEST_BYTES, timeout)
    except GatewayRejected as e:
        log.info("Manifest rejected: %s", e)
        return None
    except (httpx2.HTTPError, TimeoutError) as e:
        raise ManifestUnavailable(f"{url} ({type(e).__name__})") from e
    if status == 429 or status >= 500:
        raise ManifestUnavailable(f"{url} answered {status}")
    return body if status == 200 and is_manifest_store(body) else None


def expires_at(_key, value, now):
    # type: (str, tuple[bool, int], float) -> float
    """Expiry time of a cached probe result, which carries its own lifetime."""
    return now + value[1]


def new_cache(maxsize=10_000):
    # type: (int) -> TLRUCache
    """Create a cache of probe results per gateway URL whose entries expire after their own lifetime."""
    return TLRUCache(maxsize=maxsize, ttu=expires_at)


def new_client(transport, timeout, user_agent):
    # type: (httpx2.AsyncBaseTransport, float, str) -> httpx2.AsyncClient
    """Create the HTTP client for gateway and manifest fetches; it rejects cookies, so upstreams leave no state."""
    return httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        trust_env=False,
        cookies=CookieJar(DefaultCookiePolicy(allowed_domains=[])),
        event_hooks={"request": [require_https]},
        headers={"User-Agent": user_agent},
    )
