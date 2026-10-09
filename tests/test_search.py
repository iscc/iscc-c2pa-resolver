"""Tests for the aggregator client, using a real captured testnet response."""

import httpx2
import pytest

from iscc_c2pa_resolver.search import SearchHit, SearchUnavailable, gateway_of, is_iscc_id, is_ready, search
from tests.conftest import CONTENT, ID_EARLY, SEARCH_PATH, SEARCH_URL

URL = SEARCH_URL + SEARCH_PATH


def refuse(request):
    # type: (httpx2.Request) -> httpx2.Response
    """Transport handler that simulates an unreachable aggregator."""
    raise httpx2.ConnectError("down")


def client_for(net):
    # type: (object) -> httpx2.AsyncClient
    """An aggregator client that talks to the fake network."""
    return httpx2.AsyncClient(base_url=SEARCH_URL, transport=net.transport)  # type: ignore[attr-defined]


async def test_search_parses_live_response(net, live_search_response):
    net.json(URL, live_search_response)
    async with client_for(net) as client:
        hits = await search(client, "idp", [CONTENT], 50)
    assert hits == [
        SearchHit(
            iscc_id=ID_EARLY,
            types={"CONTENT_TEXT_V0": 1.0},
            metadata={"gateway": "https://titusz.org/iscc/gateway/maigkv5faaxoxyab.json"},
        )
    ]
    assert hits[0].gateway == "https://titusz.org/iscc/gateway/maigkv5faaxoxyab.json"


async def test_search_sends_units_and_limit(net, live_search_response):
    net.json(URL, live_search_response)
    async with client_for(net) as client:
        await search(client, "idp", [CONTENT], 50)
    request = net.sent_to(URL)[0]
    assert request.url.params["limit"] == "50"
    assert request.read() == b'{"units":["' + CONTENT.encode() + b'"]}'


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(500),
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, json={"global_matches": [{"iscc_id": "ISCC:"}]}),
        httpx2.Response(200, json={"global_matches": [{"iscc_id": CONTENT}]}),  # a unit, not an ISCC-ID
        httpx2.Response(200, json={"global_matches": [{"iscc_id": "MAIGKV5FAAXOXYAB"}]}),  # no prefix
    ],
)
async def test_search_unavailable(net, response):
    net.routes[URL] = response
    async with client_for(net) as client:
        with pytest.raises(SearchUnavailable):
            await search(client, "idp", [CONTENT], 10)


async def test_search_unreachable():
    transport = httpx2.MockTransport(refuse)
    async with httpx2.AsyncClient(base_url=SEARCH_URL, transport=transport) as client:
        with pytest.raises(SearchUnavailable):
            await search(client, "idp", [CONTENT], 10)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ID_EARLY, True),
        ("ISCC:MEIGKUAF5OHZOQAC", True),  # mainnet
        ("ISCC:maigkv5faaxoxyab", False),  # not canonical
        ("MAIGKV5FAAXOXYAB", False),
        ("ISCC:MAIGKV5FAAXOXYA", False),
        ("ISCC:XXXXXXXXXXXXXXXX", False),  # base32, but not an ISCC-ID
        ("ISCC:EAD2RASIYU5IKLEN", False),  # a Content-Code header
        (CONTENT, False),
    ],
)
def test_is_iscc_id(value, expected):
    assert is_iscc_id(value) is expected


ASSET_URL = f"{SEARCH_URL}/indexes/idp/assets/{ID_EARLY}"
GATEWAY = "https://titusz.org/iscc/gateway/maigkv5faaxoxyab.json"


async def test_gateway_of(net):
    net.declaration(ID_EARLY, GATEWAY)
    async with client_for(net) as client:
        assert await gateway_of(client, "idp", ID_EARLY) == GATEWAY


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(404, json={"detail": "Asset not found in index"}),
        httpx2.Response(400, json={"detail": "Realm mismatch"}),
        httpx2.Response(200, json={"iscc_id": ID_EARLY, "units": []}),  # no gateway
    ],
)
async def test_gateway_of_unknown(net, response):
    net.routes[ASSET_URL] = response
    async with client_for(net) as client:
        assert await gateway_of(client, "idp", ID_EARLY) is None


@pytest.mark.parametrize("response", [httpx2.Response(503), httpx2.Response(200, text="not json")])
async def test_gateway_of_unavailable(net, response):
    net.routes[ASSET_URL] = response
    async with client_for(net) as client:
        with pytest.raises(SearchUnavailable):
            await gateway_of(client, "idp", ID_EARLY)


@pytest.mark.parametrize("metadata", [None, {}, {"gateway": 42}])
def test_hit_without_gateway(metadata):
    assert SearchHit(iscc_id=ID_EARLY, metadata=metadata).gateway is None


async def test_is_ready(net):
    async with client_for(net) as client:
        assert await is_ready(client) is True
    net.json(SEARCH_URL + "/readyz", {"status": "not_ready"}, status=503)
    async with client_for(net) as client:
        assert await is_ready(client) is False


async def test_is_ready_unreachable():
    transport = httpx2.MockTransport(refuse)
    async with httpx2.AsyncClient(base_url=SEARCH_URL, transport=transport) as client:
        assert await is_ready(client) is False
