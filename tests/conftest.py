"""Shared fixtures: real ISCC test vectors, a captured aggregator response, a real C2PA Manifest Store, and a
scriptable fake network.
"""

import base64
import json
import pathlib

import httpx2
import iscc_core as ic
import pytest
from fastapi.testclient import TestClient

from iscc_c2pa_resolver.app import build_context, create_app
from iscc_c2pa_resolver.settings import Settings

DATA = pathlib.Path(__file__).parent / "data"

# IEP-0020 Test Vector 1: Meta-, Content-Text-, Data- and Instance-Code, 256 bits each
META = "ISCC:AADVPDD4R6733NMPFH3D57VTQ4KVH6VHL74XIWGUT37V5ZG56NV3NSI"
CONTENT = "ISCC:EAD2RASIYU5IKLENP2OFI4CHZGRWYQCSW2WKX3Y6FJGOCXSYNYGLGBI"
DATA_CODE = "ISCC:GADQLNA7GRZESMRF2J7NZPNWGI3II2ST5YUN5SS6GVQ2ZQGJXPPYDNI"
INSTANCE = "ISCC:IAD2KIVPJIWJZP3KQCESJL6SVT5APEZUPOJWM6HVTAXCF7OT3VFA4NY"
VECTOR_1 = (
    "AAdXjHyPv721jyn2Pv6zhxVT+qdf+XRY1J7/XuTd82u2ySAHqIJIxTqFLI1+nFRwR8mjbEBStqyr7x4qTOFeWG4MswUwBwW0HzRySTIl0n7cvbYyNoRq"
    "U+4o3speNWGswMm734G1QAelIq9KLJy/aoCJJK/SrPoHkzR7k2Z49ZguIv3T3UoONw=="
)

SEARCH_URL = "https://search.example"
SEARCH_PATH = "/indexes/idp/search"
ID_EARLY = "ISCC:MAIGKV5FAAXOXYAB"  # real testnet declaration
ID_LATE = "ISCC:MAIGKV5N6NTVF4AB"  # real declaration on titusz.org, issued later
MANIFEST_ID = "urn:c2pa:F9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4"
MANIFEST_URL = f"https://repo.example/v1/manifests/{MANIFEST_ID}"  # declared as gateway URL, as docs/gateway.md says
# The real gateway URL of ISCC:MAIGKV5FAAXOXYAB, an ordinary gateway document without a C2PA Manifest.
TITUSZ_GATEWAY_URL = "https://titusz.org/iscc/gateway/maigkv5faaxoxyab.json"
# A real C2PA Manifest Store from the c2pa-rs test fixtures (sdk/tests/fixtures/ingredient/manifest_data.c2pa,
# commit 58b9a09, Apache-2.0 or MIT).
MANIFEST = (DATA / "manifest-c2pa-rs.c2pa").read_bytes()


def encode(units):
    # type: (list[str]) -> str
    """Encode ISCC-UNITs as a base64 `io.iscc.v0` value."""
    return base64.b64encode(ic.encode_seq(units)).decode("ascii")


def hit(iscc_id, gateway, **types):
    # type: (str, str | None, float) -> dict
    """An aggregator search hit in the shape iscc-search returns."""
    return {"iscc_id": iscc_id, "score": 1.0, "types": types, "metadata": {"gateway": gateway} if gateway else None}


class FakeNetwork:
    """Answers HTTP requests from a URL table and records every request it receives."""

    def __init__(self):
        self.routes = {}  # type: dict[str, httpx2.Response]
        self.requests = []  # type: list[httpx2.Request]

    def handle(self, request):
        # type: (httpx2.Request) -> httpx2.Response
        """Return the configured response for the URL without query, or 404."""
        self.requests.append(request)
        return self.routes.get(str(request.url.copy_with(query=None)), httpx2.Response(404))

    def json(self, url, data, status=200):
        # type: (str, object, int) -> None
        """Serve JSON at a URL."""
        self.routes[url] = httpx2.Response(status, json=data)

    def hits(self, *hits):
        # type: (dict) -> None
        """Let the aggregator answer searches with the given hits."""
        self.json(SEARCH_URL + SEARCH_PATH, {"query": {}, "global_matches": list(hits), "chunk_matches": []})

    def content(self, url, data, status=200):
        # type: (str, bytes, int) -> None
        """Serve bytes at a URL."""
        self.routes[url] = httpx2.Response(status, content=data)

    def declaration(self, iscc_id, gateway):
        # type: (str, str) -> None
        """Let the aggregator know a declaration and its gateway, as `GET /indexes/{name}/assets/{iscc_id}` does."""
        self.json(f"{SEARCH_URL}/indexes/idp/assets/{iscc_id}", {"iscc_id": iscc_id, "metadata": {"gateway": gateway}})

    def manifest(self, url=MANIFEST_URL):
        # type: (str) -> None
        """Serve the C2PA Manifest Store at a manifest address."""
        self.content(url, MANIFEST)

    def sent_to(self, url):
        # type: (str) -> list[httpx2.Request]
        """Requests received for a URL, ignoring the query."""
        return [r for r in self.requests if str(r.url.copy_with(query=None)) == url]

    @property
    def transport(self):
        # type: () -> httpx2.MockTransport
        """The httpx2 transport that routes into this fake network."""
        return httpx2.MockTransport(self.handle)


@pytest.fixture
def settings():
    # type: () -> Settings
    """Settings with a fake aggregator."""
    return Settings(search_url=SEARCH_URL, search_index="idp")


@pytest.fixture
def net():
    # type: () -> FakeNetwork
    """A fake network with a ready aggregator."""
    network = FakeNetwork()
    network.json(SEARCH_URL + "/readyz", {"status": "ready"})
    return network


@pytest.fixture
def client(settings, net):
    # type: (Settings, FakeNetwork) -> TestClient
    """A test client for an app whose outbound HTTP goes to the fake network."""
    with TestClient(create_app(settings, net.transport)) as test_client:
        yield test_client


@pytest.fixture
async def ctx(settings, net):
    """A resolver context whose outbound HTTP goes to the fake network."""
    context = build_context(settings, net.transport)
    yield context
    await context.search_client.aclose()
    await context.gateway_client.aclose()


@pytest.fixture
def live_search_response():
    # type: () -> dict
    """A real response of search-test.iscc.id, captured on 2026-10-08."""
    return json.loads((DATA / "search-test-response.json").read_text())


@pytest.fixture
def titusz_gateway():
    # type: () -> dict
    """The real gateway document of ISCC:MAIGKV5FAAXOXYAB, served at TITUSZ_GATEWAY_URL; not a manifest."""
    return json.loads((DATA / "gateway-titusz-org.json").read_text(encoding="utf-8"))
