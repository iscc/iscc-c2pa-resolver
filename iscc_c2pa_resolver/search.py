"""Client for the iscc-search aggregator, which indexes the declarations of all ISCC hubs of one network."""

import logging
import re

import httpx2
import iscc_core as ic
from pydantic import BaseModel, ValidationError, field_validator

log = logging.getLogger(__name__)

ISCC_ID_FORMAT = re.compile(r"ISCC:[A-Z2-7]{16}")


class SearchUnavailable(Exception):
    """Raised when the aggregator cannot answer a request."""


def is_iscc_id(value):
    # type: (str) -> bool
    """Tell whether a string is an ISCC-ID in canonical form, safe to decode and to use in a URL path."""
    if not ISCC_ID_FORMAT.fullmatch(value):
        return False
    try:
        return ic.iscc_decode(value)[0] == ic.MT.ID
    except (ValueError, IndexError):
        return False


class SearchHit(BaseModel):
    """One declaration known to the aggregator; search hits carry per-unit-type similarity scores from 0 to 1."""

    iscc_id: str
    types: dict[str, float] = {}
    metadata: dict[str, object] | None = None

    @field_validator("iscc_id")
    @classmethod
    def check_iscc_id(cls, value):
        # type: (str) -> str
        """Accept ISCC-IDs only, so they can be decoded safely later."""
        if not is_iscc_id(value):
            raise ValueError("not an ISCC-ID")
        return value

    @property
    def gateway(self):
        # type: () -> str | None
        """The expanded gateway URL of the declaration, if it has one."""
        url = (self.metadata or {}).get("gateway")
        return url if isinstance(url, str) else None


class SearchResult(BaseModel):
    """The part of the aggregator's search response that the resolver uses."""

    global_matches: list[SearchHit] = []


async def search(client, index, units, limit):
    # type: (httpx2.AsyncClient, str, list[str], int) -> list[SearchHit]
    """Search an aggregator index for declarations similar to the given ISCC-UNITs.

    :raises SearchUnavailable: on network errors, non-2xx answers and malformed responses.
    """
    try:
        response = await client.post(f"/indexes/{index}/search", params={"limit": limit}, json={"units": units})
        response.raise_for_status()
        return SearchResult.model_validate_json(response.content).global_matches
    except (httpx2.HTTPError, ValidationError) as e:
        log.warning("Search backend failed: %s", type(e).__name__)
        raise SearchUnavailable from e


async def gateway_of(client, index, iscc_id):
    # type: (httpx2.AsyncClient, str, str) -> str | None
    """Return the gateway URL of a declaration, or None if the index does not hold it or it has no gateway.

    The aggregator answers 404 for unknown ISCC-IDs and 400 for ISCC-IDs of another network.

    :raises SearchUnavailable: on network errors, other non-2xx answers and malformed responses.
    """
    try:
        response = await client.get(f"/indexes/{index}/assets/{iscc_id}")
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return SearchHit.model_validate_json(response.content).gateway
    except (httpx2.HTTPError, ValidationError) as e:
        log.warning("Search backend failed: %s", type(e).__name__)
        raise SearchUnavailable from e


async def is_ready(client):
    # type: (httpx2.AsyncClient) -> bool
    """Tell whether the aggregator reports itself ready."""
    try:
        response = await client.get("/readyz")
    except httpx2.HTTPError:
        return False
    return response.status_code == 200
