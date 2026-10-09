"""Tests for the query pipeline (scoring, gateway URL probes, merging, ranking) and for fetching manifests."""

import pytest

from iscc_c2pa_resolver.resolve import backend_ready, declaration_path, fetch_manifest, find_matches, similarity_score
from iscc_c2pa_resolver.schema import IsccMatch
from tests.conftest import (
    CONTENT,
    ID_EARLY,
    ID_LATE,
    MANIFEST,
    MANIFEST_ID,
    MANIFEST_URL,
    SEARCH_PATH,
    SEARCH_URL,
    TITUSZ_GATEWAY_URL,
    hit,
)

API = "https://resolver.example/v1"

OTHER_ID = "urn:c2pa:2B7D5E1A-0000-4000-8000-000000000000"
OTHER_URL = f"https://b.example/api/manifests/{OTHER_ID}"
MIRROR_URL = f"https://mirror.example/v1/manifests/{MANIFEST_ID}"  # the same manifest ID at another repository


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        ({"INSTANCE_NONE_V0": 1.0, "CONTENT_IMAGE_V0": 0.9}, 100),
        ({"CONTENT_IMAGE_V0": 0.93, "DATA_NONE_V0": 0.81}, 93),
        ({"SEMANTIC_TEXT_V0": 0.5}, 50),
        ({"DATA_NONE_V0": 0.995}, 99),  # only an equal Instance-Code scores 100
        ({"CONTENT_TEXT_V0": 0.005}, 1),  # rounds half up
        ({"CONTENT_TEXT_V0": 0.0}, 0),
        ({"META_NONE_V0": 1.0}, None),  # metadata alone never matches
        ({}, None),
    ],
)
def test_similarity_score(types, expected):
    assert similarity_score(types) == expected


def test_declaration_path():
    assert declaration_path(ID_EARLY) == "maigkv5faaxoxyab"


async def test_find_matches(ctx, net):
    net.hits(hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.93))
    net.manifest()
    assert await find_matches(ctx, [CONTENT], 10, API) == [
        IsccMatch(
            manifestId=MANIFEST_ID,
            endpoint=f"{API}/iscc/maigkv5faaxoxyab",
            similarityScore=93,
            isccId=ID_EARLY,
        )
    ]
    assert ctx.recent[MANIFEST_ID] == ID_EARLY


async def test_fetch_manifest(ctx, net):
    net.declaration(ID_EARLY, MANIFEST_URL)
    net.manifest()
    assert await fetch_manifest(ctx, ID_EARLY, MANIFEST_ID) == MANIFEST
    assert await fetch_manifest(ctx, ID_EARLY, OTHER_ID) is None
    assert len(net.sent_to(MANIFEST_URL)) == 1  # a manifest ID that the gateway URL does not name is never fetched


async def test_fetch_manifest_declaration_without_manifest_address(ctx, net):
    net.declaration(ID_EARLY, TITUSZ_GATEWAY_URL)
    assert await fetch_manifest(ctx, ID_EARLY, MANIFEST_ID) is None
    assert net.sent_to(TITUSZ_GATEWAY_URL) == []


async def test_fetch_manifest_declaration_without_gateway(ctx, net):
    net.json(f"{SEARCH_URL}/indexes/idp/assets/{ID_EARLY}", {"iscc_id": ID_EARLY, "metadata": None})
    assert await fetch_manifest(ctx, ID_EARLY, MANIFEST_ID) is None


async def test_find_matches_oversamples(ctx, net):
    net.hits()
    await find_matches(ctx, [CONTENT], 3, API)
    await find_matches(ctx, [CONTENT], 50, API)
    limits = [r.url.params["limit"] for r in net.sent_to(SEARCH_URL + SEARCH_PATH)]
    assert limits == ["15", "100"]


async def test_find_matches_merges_manifest_keeping_best(ctx, net):
    net.hits(hit(ID_LATE, MANIFEST_URL, CONTENT_TEXT_V0=0.8), hit(ID_EARLY, MANIFEST_URL, INSTANCE_NONE_V0=1.0))
    net.manifest()
    assert await find_matches(ctx, [CONTENT], 10, API) == [
        IsccMatch(manifestId=MANIFEST_ID, endpoint=f"{API}/iscc/maigkv5faaxoxyab", similarityScore=100, isccId=ID_EARLY)
    ]


async def test_find_matches_keeps_equal_ids_of_different_repositories(ctx, net):
    net.hits(hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.9), hit(ID_LATE, MIRROR_URL, CONTENT_TEXT_V0=0.8))
    net.manifest()
    net.manifest(MIRROR_URL)
    matches = await find_matches(ctx, [CONTENT], 10, API)
    assert [(m.manifestId, m.isccId) for m in matches] == [(MANIFEST_ID, ID_EARLY), (MANIFEST_ID, ID_LATE)]
    assert ctx.recent[MANIFEST_ID] == ID_EARLY


async def test_find_matches_earlier_declaration_wins_ties(ctx, net):
    net.hits(hit(ID_LATE, MANIFEST_URL, CONTENT_TEXT_V0=0.9), hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.9))
    net.manifest()
    matches = await find_matches(ctx, [CONTENT], 10, API)
    assert [m.isccId for m in matches] == [ID_EARLY]


async def test_find_matches_ranks_and_cuts(ctx, net):
    net.hits(hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.8), hit(ID_LATE, OTHER_URL, CONTENT_TEXT_V0=0.95))
    net.manifest()
    net.manifest(OTHER_URL)
    assert [m.manifestId for m in await find_matches(ctx, [CONTENT], 10, API)] == [OTHER_ID, MANIFEST_ID]
    assert [m.manifestId for m in await find_matches(ctx, [CONTENT], 1, API)] == [OTHER_ID]


async def test_find_matches_skips_unusable_hits(ctx, net, titusz_gateway):
    net.hits(
        hit(ID_EARLY, None, CONTENT_TEXT_V0=1.0),  # no gateway
        hit(ID_EARLY, MANIFEST_URL, META_NONE_V0=1.0),  # metadata only
        hit(ID_LATE, TITUSZ_GATEWAY_URL, CONTENT_TEXT_V0=1.0),  # ordinary gateway document
        hit(ID_LATE, OTHER_URL, CONTENT_TEXT_V0=1.0),  # a manifest address that does not serve a manifest
        hit(ID_LATE, "https://b.example:invalid/v1/manifests/x", CONTENT_TEXT_V0=1.0),  # malformed
    )
    net.manifest()
    net.json(TITUSZ_GATEWAY_URL, titusz_gateway)
    net.json(OTHER_URL, titusz_gateway)
    assert await find_matches(ctx, [CONTENT], 10, API) == []
    assert net.sent_to(MANIFEST_URL) == []
    assert net.sent_to(TITUSZ_GATEWAY_URL) == []  # never fetched
    assert len(net.sent_to(OTHER_URL)) == 1


async def test_find_matches_ignores_live_network_without_manifests(ctx, net, live_search_response):
    net.json(SEARCH_URL + SEARCH_PATH, live_search_response)
    assert await find_matches(ctx, [CONTENT], 10, API) == []
    assert [str(r.url.copy_with(query=None)) for r in net.requests] == [SEARCH_URL + SEARCH_PATH]


async def test_find_matches_caches_probes(ctx, net):
    net.hits(hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.93), hit(ID_LATE, OTHER_URL, CONTENT_TEXT_V0=0.9))
    net.manifest()
    net.content(OTHER_URL, b"<html>not a manifest</html>")
    await find_matches(ctx, [CONTENT], 10, API)
    assert len(await find_matches(ctx, [CONTENT], 10, API)) == 1
    assert len(net.sent_to(MANIFEST_URL)) == 1
    assert len(net.sent_to(OTHER_URL)) == 1


async def test_backend_ready_is_cached(ctx, net):
    assert await backend_ready(ctx) is True
    net.json(SEARCH_URL + "/readyz", {}, status=503)
    assert await backend_ready(ctx) is True
    ctx.status_cache.clear()
    assert await backend_ready(ctx) is False
