"""HTTP API of the resolver: the query, fetch and service routes of the C2PA Soft Binding Resolution API.

The API contract is the hand-written `openapi/openapi.yaml`, whose `c2pa.*` schemas come verbatim from the C2PA
OpenAPI document. Request and response models in `schema/` are generated from it, and FastAPI's own spec generation
is switched off so that the served spec is the hand-written one.
"""

import asyncio
import contextlib
import html
import json
import mimetypes
import typing  # noqa: F401 (used in type comments)
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx2
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from iscc_c2pa_resolver import __version__, binding, gateway, netguard, resolve, search
from iscc_c2pa_resolver.schema import IsccQueryResult
from iscc_c2pa_resolver.schema.c2pa import (
    Fingerprint,
    ServiceCapabilities,
    ServiceStatus,
    SoftBindingAlgList,
    SoftBindingQuery,
    Status,
    WellKnownDiscovery,
)
from iscc_c2pa_resolver.settings import Settings

HERE = Path(__file__).parent
STATIC = HERE / "static"
API_PREFIX = "/v1"
C2PA_OPENAPI = HERE / "openapi" / "c2pa-sbr.json"
# The C2PA specification requires the reported version to equal `info.version` of the implemented API definition
SPEC_VERSION = json.loads(C2PA_OPENAPI.read_text(encoding="utf-8"))["info"]["version"]
STATUS_TTL = 15  # seconds between search backend probes
MAX_BODY_BYTES = 16 * 1024
NOT_FOUND = "C2PA Manifest not found"
USER_AGENT = f"iscc-c2pa-resolver/{__version__} (+https://github.com/iscc/iscc-c2pa-resolver)"
MaxResults = Annotated[int, Query(ge=1)]
NETWORKS = {"idp": "mainnet", "idptest": "testnet"}  # aggregator index -> ISCC network
mimetypes.add_type("application/yaml", ".yaml")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("font/woff2", ".woff2")


class BodyLimit:
    """ASGI middleware that refuses request bodies above a size limit and bodies without Content-Length."""

    def __init__(self, app, limit):
        # type: (object, int) -> None
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        # type: (dict, object, object) -> None
        """Reject oversized or unsized bodies before the application reads them."""
        headers = dict(scope.get("headers", [])) if scope["type"] == "http" else {}
        length = headers.get(b"content-length")
        if b"transfer-encoding" in headers:
            response = JSONResponse({"detail": "Content-Length required"}, status_code=411)
        elif length is not None and (not length.isdigit() or int(length) > self.limit):
            response = JSONResponse({"detail": "Request body too large"}, status_code=413)
        else:
            return await self.app(scope, receive, send)  # type: ignore[operator]
        await response(scope, receive, send)  # type: ignore[arg-type]


def get_ctx(request: Request) -> resolve.Context:
    """Dependency that returns the resolver context of the running app."""
    return request.app.state.ctx


Ctx = Annotated[resolve.Context, Depends(get_ctx)]


def api_base(request):
    # type: (Request) -> str
    """Absolute URL of the versioned API as seen by the client."""
    return str(request.base_url).rstrip("/") + API_PREFIX


async def answer(request, alg, value, max_results):
    # type: (Request, str, str, int) -> IsccQueryResult
    """Run a soft binding query and map failures to HTTP errors."""
    ctx = get_ctx(request)
    if alg != binding.ALG:
        raise HTTPException(400, f"Unsupported alg; this service supports {binding.ALG}")
    try:
        units = binding.decode_value(value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        matches = await resolve.find_matches(ctx, units, min(max_results, resolve.MAX_RESULTS), api_base(request))
    except search.SearchUnavailable as e:
        raise HTTPException(500, "Search backend unavailable") from e
    return IsccQueryResult(matches=matches)


async def query_by_binding(
    request: Request,
    alg: Annotated[str, Query(max_length=64)],
    value: Annotated[str, Query(max_length=binding.MAX_VALUE_LENGTH)],
    maxResults: MaxResults = 10,
) -> IsccQueryResult:
    """Find C2PA Manifests for an `io.iscc.v0` soft binding value."""
    return await answer(request, alg, value, maxResults)


async def query_by_large_binding(
    request: Request, query: SoftBindingQuery, maxResults: MaxResults = 10
) -> IsccQueryResult:
    """Find C2PA Manifests for an `io.iscc.v0` soft binding value sent in a JSON body."""
    return await answer(request, query.alg, query.value, maxResults)


async def serve_manifest(ctx, iscc_id, manifest_id):
    # type: (resolve.Context, str, str) -> Response
    """Pass the C2PA Manifest Store of a declaration through, mapping failures to HTTP errors."""
    try:
        data = await resolve.fetch_manifest(ctx, iscc_id, manifest_id)
    except search.SearchUnavailable as e:
        raise HTTPException(500, "Search backend unavailable") from e
    except gateway.ManifestUnavailable as e:
        raise HTTPException(500, "Manifest repository unavailable") from e
    if data is None:
        raise HTTPException(404, NOT_FOUND)
    return Response(data, media_type="application/c2pa")


async def get_declared_manifest(
    ctx: Ctx, isccId: str, activeManifestId: str, returnActiveManifest: bool = False
) -> Response:
    """Serve the C2PA Manifest Store that a declaration points to; the `endpoint` of a match leads here."""
    iscc_id = "ISCC:" + isccId.upper()
    if not search.is_iscc_id(iscc_id):
        raise HTTPException(404, NOT_FOUND)
    return await serve_manifest(ctx, iscc_id, activeManifestId)


async def get_manifest(ctx: Ctx, activeManifestId: str, returnActiveManifest: bool = False) -> Response:
    """Serve the C2PA Manifest Store of a manifest ID that a query to this process returned in the last hour."""
    iscc_id = ctx.recent.get(activeManifestId)
    if iscc_id is None:
        raise HTTPException(404, NOT_FOUND)
    return await serve_manifest(ctx, iscc_id, activeManifestId)


async def supported_algorithms() -> SoftBindingAlgList:
    """List the soft binding algorithms accepted in queries."""
    return SoftBindingAlgList(watermarks=[], fingerprints=[Fingerprint(alg=binding.ALG)])


async def capabilities() -> ServiceCapabilities:
    """Report the implemented specification version; no optional capability is supported."""
    return ServiceCapabilities(c2paSpecificationVersion=SPEC_VERSION, supportedCapabilities=[])


async def status(ctx: Ctx) -> ServiceStatus:
    """Report `ok`, or `degraded` while the search backend is not ready."""
    ready = await resolve.backend_ready(ctx)
    return ServiceStatus(
        status=Status.ok if ready else Status.degraded, timestamp=datetime.now(UTC).replace(microsecond=0)
    )


async def discovery(request: Request) -> WellKnownDiscovery:
    """Point clients to the versioned API, with absolute URLs as the schema's `format: uri` requires."""
    api = api_base(request)
    return WellKnownDiscovery(
        apiEndpoint=api,  # type: ignore[arg-type]
        c2paSpecificationVersion=SPEC_VERSION,
        capabilitiesEndpoint=f"{api}/services/capabilities",  # type: ignore[arg-type]
        statusEndpoint=f"{api}/services/status",  # type: ignore[arg-type]
    )


def network_of(index):
    # type: (str) -> str
    """Name of the ISCC network an aggregator index serves; other indexes go by their own name."""
    return NETWORKS.get(index, index)


def landing_page(network):
    # type: (str) -> str
    """The landing page with the network and the app version filled in."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    return page.replace("{{network}}", html.escape(network)).replace("{{version}}", html.escape(__version__))


async def root(request: Request) -> Response:
    """Serve the landing page to browsers and a JSON summary to everyone else."""
    network = network_of(get_ctx(request).settings.search_index)
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(landing_page(network), headers={"Vary": "Accept"})
    summary = {
        "service": "ISCC C2PA Resolver",
        "version": __version__,
        "network": network,
        "api": API_PREFIX,
        "discovery": "/.well-known/c2pa-soft-binding-resolution",
        "openapi": "/openapi/openapi.json",
        "docs": "/docs",
    }
    return JSONResponse(summary, headers={"Vary": "Accept"})


async def docs() -> FileResponse:
    """Serve the interactive API reference."""
    return FileResponse(STATIC / "docs.html")


async def healthz() -> dict[str, str]:
    """Liveness probe that does not depend on the search backend."""
    return {"status": "ok"}


async def bad_request(_request, exc):
    # type: (Request, Exception) -> JSONResponse
    """Answer invalid parameters with 400, as the C2PA API specifies, instead of FastAPI's 422."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"]} for e in errors]}, status_code=400)


def allowed_methods(request):
    # type: (Request) -> list[str]
    """Methods that any route accepts for the request path; FastAPI keeps one route per method."""
    methods = set()  # type: set[str]
    for route in request.app.routes:
        if route.matches(request.scope)[0] != Match.NONE:
            methods |= getattr(route, "methods", None) or set()
    return sorted(methods)


async def http_error(request, exc):
    # type: (Request, Exception) -> JSONResponse
    """Render HTTP errors as JSON; a 405 lists every allowed method in `Allow`, as RFC 9110 requires."""
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 (registered for this type only)
    headers = dict(exc.headers or {})
    if exc.status_code == 405:
        headers["Allow"] = ", ".join(allowed_methods(request))
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


def build_context(settings, transport=None):
    # type: (Settings, httpx2.AsyncBaseTransport | None) -> resolve.Context
    """Create the long-lived resources; `transport` replaces all outbound HTTP (used by tests)."""
    headers = {"User-Agent": USER_AGENT}
    return resolve.Context(
        settings=settings,
        search_client=httpx2.AsyncClient(
            base_url=settings.search_url,
            timeout=settings.search_timeout,
            transport=transport,
            trust_env=False,
            headers=headers,
        ),
        gateway_client=gateway.new_client(
            transport or netguard.public_transport(settings.gateway_concurrency * 2),
            settings.gateway_timeout,
            USER_AGENT,
        ),
        gateway_cache=gateway.new_cache(),
        gateway_slots=asyncio.Semaphore(settings.gateway_concurrency),
        status_cache=TTLCache(maxsize=1, ttl=STATUS_TTL),
        recent=resolve.new_recent(),
    )


@contextlib.asynccontextmanager
async def lifespan(app):
    # type: (FastAPI) -> typing.AsyncIterator[None]
    """Open the resolver context on startup and release it on shutdown."""
    ctx = build_context(app.state.settings, app.state.transport)
    app.state.ctx = ctx
    try:
        yield
    finally:
        await ctx.search_client.aclose()
        await ctx.gateway_client.aclose()


ROUTES = [
    (f"{API_PREFIX}/matches/byBinding", query_by_binding, "GET"),
    (f"{API_PREFIX}/matches/byBinding", query_by_large_binding, "POST"),
    (f"{API_PREFIX}/manifests/{{activeManifestId:path}}", get_manifest, "GET"),  # C2PA URNs may contain "/"
    (f"{API_PREFIX}/iscc/{{isccId}}/manifests/{{activeManifestId:path}}", get_declared_manifest, "GET"),
    (f"{API_PREFIX}/services/supportedAlgorithms", supported_algorithms, "GET"),
    (f"{API_PREFIX}/services/capabilities", capabilities, "GET"),
    (f"{API_PREFIX}/services/status", status, "GET"),
    ("/.well-known/c2pa-soft-binding-resolution", discovery, "GET"),
    ("/", root, "GET"),
    ("/docs", docs, "GET"),
    ("/healthz", healthz, "GET"),
]


def create_app(settings=None, transport=None):
    # type: (Settings | None, httpx2.AsyncBaseTransport | None) -> FastAPI
    """Create the resolver application; `transport` replaces all outbound HTTP (used by tests)."""
    app = FastAPI(
        title="ISCC C2PA Resolver",
        version=__version__,
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings or Settings()  # type: ignore[call-arg]
    app.state.transport = transport
    for path, endpoint, method in ROUTES:
        app.add_api_route(path, endpoint, methods=[method], response_model_exclude_none=True)
    app.mount("/openapi", StaticFiles(directory=HERE / "openapi"), name="openapi")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_exception_handler(RequestValidationError, bad_request)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES)  # type: ignore[arg-type]
    return app


app = create_app()
