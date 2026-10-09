"""Query pipeline: search the aggregator, keep the hits whose gateway URL is a manifest address, and turn them into
C2PA matches.

Also fetches the manifest of a match: the declaration and its gateway URL are read again on every fetch, so nothing
has to be stored. The only memory is a short-lived, per-process map from recently returned manifest IDs to their
declarations, for clients that ignore the `endpoint` of a match.
"""

import asyncio
import math
from dataclasses import dataclass

import httpx2
import iscc_core as ic
from cachetools import TLRUCache, TTLCache

from iscc_c2pa_resolver import gateway, search
from iscc_c2pa_resolver.schema import IsccMatch
from iscc_c2pa_resolver.settings import Settings

MAX_RESULTS = 100
OVERSAMPLE = 5  # most declarations carry no manifest, so ask for more candidates than results
SIMILARITY_TYPES = ("SEMANTIC_", "CONTENT_", "DATA_")
RECENT_TTL = 3600  # seconds a returned manifest ID stays fetchable without `endpoint`
RECENT_MAX = 100_000


@dataclass(frozen=True)
class Context:
    """Long-lived resources of one resolver process."""

    settings: Settings
    search_client: httpx2.AsyncClient
    gateway_client: httpx2.AsyncClient
    gateway_cache: TLRUCache  # gateway URL -> (serves a manifest store, cache lifetime)
    gateway_slots: asyncio.Semaphore
    status_cache: TTLCache
    recent: TTLCache  # manifest ID -> ISCC-ID of the declaration that a recent query returned


@dataclass(frozen=True)
class Candidate:
    """A search hit whose gateway URL serves a manifest."""

    score: int
    iscc_id: str
    pointer: gateway.Pointer


def similarity_score(types):
    # type: (dict[str, float]) -> int | None
    """Score a hit from its per-unit-type scores: 100 for an equal Instance-Code, otherwise the best Semantic-,
    Content- or Data-Code similarity as an integer of at most 99. None if no such unit matched.
    """
    if any(name.startswith("INSTANCE_") and value >= 1.0 for name, value in types.items()):
        return 100
    scores = [value for name, value in types.items() if name.startswith(SIMILARITY_TYPES)]
    if not scores:
        return None
    return min(99, max(0, math.floor(max(scores) * 100 + 0.5)))


def rank_key(candidate):
    # type: (Candidate) -> tuple[int, bytes]
    """Order by score, then by ISCC-ID, which puts the earlier declaration first among equal scores."""
    return -candidate.score, ic.decode_base32(candidate.iscc_id.removeprefix("ISCC:"))


async def lookup(ctx, url):
    # type: (Context, str) -> gateway.Pointer | None
    """Return the manifest pointer of a gateway URL if it serves a manifest store, probing it unless cached.

    Gateway URLs that are not manifest addresses are dismissed without a request.
    """
    pointer = gateway.parse_pointer(url)
    if pointer is None:
        return None
    probed = ctx.gateway_cache.get(url)
    if probed is None:
        async with ctx.gateway_slots:
            probed = await gateway.probe_manifest(ctx.gateway_client, url, ctx.settings.gateway_timeout)
        ctx.gateway_cache[url] = probed
    return pointer if probed[0] else None


def declaration_path(iscc_id):
    # type: (str) -> str
    """Path segment of a declaration in `endpoint` URLs: the ISCC-ID in lowercase without prefix."""
    return iscc_id.removeprefix("ISCC:").lower()


def new_recent():
    # type: () -> TTLCache
    """Create the map from recently returned manifest IDs to their declarations."""
    return TTLCache(maxsize=RECENT_MAX, ttl=RECENT_TTL)


async def find_matches(ctx, units, max_results, api):
    # type: (Context, list[str], int, str) -> list[IsccMatch]
    """Find C2PA Manifests whose declarations match the given ISCC-UNITs, best first.

    Hits are merged per manifest address, keeping the best. Each match points to `{api}/iscc/{declaration}` as its
    endpoint. Returned manifest IDs are remembered for an hour; the first declaration seen for an ID keeps it.

    :raises search.SearchUnavailable: if the aggregator cannot answer.
    """
    limit = min(max_results * OVERSAMPLE, MAX_RESULTS)
    hits = await search.search(ctx.search_client, ctx.settings.search_index, units, limit)
    scored = [(hit, score) for hit in hits if hit.gateway and (score := similarity_score(hit.types)) is not None]
    pointers = await asyncio.gather(*(lookup(ctx, hit.gateway) for hit, _ in scored))  # type: ignore[arg-type]
    candidates = [
        Candidate(score=score, iscc_id=hit.iscc_id, pointer=pointer)
        for (hit, score), pointer in zip(scored, pointers, strict=True)
        if pointer is not None
    ]
    best = {}  # type: dict[str, Candidate]
    for candidate in sorted(candidates, key=rank_key):
        best.setdefault(candidate.pointer.manifest_url, candidate)
    ranked = list(best.values())[:max_results]
    for c in ranked:
        ctx.recent.setdefault(c.pointer.manifest_id, c.iscc_id)
    return [
        IsccMatch(
            manifestId=c.pointer.manifest_id,
            endpoint=f"{api}/iscc/{declaration_path(c.iscc_id)}",
            similarityScore=c.score,
            isccId=c.iscc_id,
        )
        for c in ranked
    ]


async def fetch_manifest(ctx, iscc_id, manifest_id):
    # type: (Context, str, str) -> bytes | None
    """Return the C2PA Manifest Store that a declaration points to, or None if it does not point to `manifest_id`.

    :raises search.SearchUnavailable: if the aggregator cannot answer.
    :raises gateway.ManifestUnavailable: if the manifest repository cannot answer.
    """
    url = await search.gateway_of(ctx.search_client, ctx.settings.search_index, iscc_id)
    pointer = gateway.parse_pointer(url) if url else None
    if pointer is None or pointer.manifest_id != manifest_id:
        return None
    async with ctx.gateway_slots:
        return await gateway.fetch_manifest(ctx.gateway_client, pointer.manifest_url, ctx.settings.manifest_timeout)


async def backend_ready(ctx):
    # type: (Context) -> bool
    """Tell whether the search backend is ready, probing it at most every few seconds."""
    ready = ctx.status_cache.get("ready")
    if ready is None:
        ready = await search.is_ready(ctx.search_client)
        ctx.status_cache["ready"] = ready
    return ready
