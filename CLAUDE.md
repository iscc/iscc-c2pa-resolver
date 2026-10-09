# CLAUDE.md

Guidance for coding agents working in this repository.

## Project

**iscc-c2pa-resolver** is a FastAPI service that implements the query, fetch and service routes of the C2PA Soft
Binding Resolution API (upcoming specification 2.5, whose OpenAPI document still says 2.4.0) for the fingerprint
algorithm `io.iscc.v0`. It is a read-only search client of the ISCC Discovery Protocol: it searches an iscc-search
aggregator and returns the hits whose gateway URL is a manifest address on a Soft Binding Resolution API
(`https://…/manifests/{manifestId}`) that serves a C2PA Manifest Store; other gateway URLs are never fetched. It
never writes declarations and stores nothing between requests: the `endpoint` of each match names the declaration,
so fetching a manifest repeats the lookup and passes the manifest bytes through. A one-hour, per-process map of
recently returned manifest IDs serves clients that ignore `endpoint`.

Public instances: `c2pa.iscc.io` (mainnet, aggregator `search.iscc.io`, index `idp`) and `c2pa-test.iscc.io`
(testnet, aggregator `search-test.iscc.id`, index `idptest`).

## Commands

```bash
uv sync                     # dev and docs dependencies
uv run prek install         # git hooks (once per clone)
uv run poe all              # format, type check, test (local loop)
uv run poe ci               # all gates without modifying files
uv run poe test             # pytest, 100% branch coverage required
uv run pytest tests/test_binding.py::test_decode_value_is_lenient
uv run poe serve            # dev server on 127.0.0.1:45460
uv run poe conformance      # C2PA conformance harness against the dev server (needs Node)
uv run poe docs-serve       # docs preview on 127.0.0.1:45461
uv run poe docs-build       # docs site into site/, with the Markdown copies of the pages
uv run poe docs-check       # built docs against the ISCC theme rules
uv run poe codegen          # regenerate openapi.json and schema/ from openapi/openapi.yaml
uv run poe sync-c2pa <dir>  # refresh openapi/c2pa-sbr.json from a C2PA specs-core checkout
```

## Layout

- `iscc_c2pa_resolver/openapi/openapi.yaml` - hand-written API contract (source of truth)
- `iscc_c2pa_resolver/openapi/openapi.json` - the same contract as JSON for browsers (`uv run poe codegen`); never
    edit
- `iscc_c2pa_resolver/openapi/c2pa-sbr.json` - verbatim C2PA OpenAPI subset, written by `scripts/sync_c2pa_openapi.py`;
    its `info.version` is the specification version the service reports
- `iscc_c2pa_resolver/schema/` - pydantic models generated from `openapi.yaml` (`uv run poe codegen`); never edit
- `iscc_c2pa_resolver/app.py` - route table, lifespan, error mapping (400 instead of 422, 405 with `Allow`), body
    limit, CORS, landing page and `/docs`
- `iscc_c2pa_resolver/static/` - landing page and API reference page; `brand/` is copied from the ISCC brand kit,
    `vendor/` holds Stoplight Elements 9.0.27 (do not edit either)
- `iscc_c2pa_resolver/binding.py` - base64 and ISCC-SEQ decoding (IEP-0020), searchable unit filter
- `iscc_c2pa_resolver/search.py` - aggregator client
- `iscc_c2pa_resolver/gateway.py` - manifest ID from gateway URLs, 32-byte manifest store probe, manifest fetch
- `iscc_c2pa_resolver/netguard.py` - outbound connections to public addresses only (SSRF protection)
- `iscc_c2pa_resolver/resolve.py` - pipeline, similarity score, ranking, probe cache, manifest lookup
- `iscc_c2pa_resolver/settings.py` - environment configuration, prefix `ISCC_C2PA_RESOLVER_`
- `tests/` - pytest; outbound HTTP goes through `tests/conftest.py::FakeNetwork`; real captured data in `tests/data`
- `docs/`, `zensical.toml` - documentation site on the ISCC theme
    ([zensical-iscc](https://github.com/iscc/zensical-iscc), docs dependency group)

## Rules

- The API is spec-first: change `openapi.yaml` first, run `uv run poe codegen`, then the code. Never edit `schema/`
    or `c2pa-sbr.json` by hand; rerun `uv run poe sync-c2pa` to follow upstream.
- HTTP client is `httpx2` (with `httpcore2`), not `httpx`.
- Type hints as PEP 484 type comments; annotations only where FastAPI or pydantic need them.
- Short pure functions, no nested functions, a docstring on every module and function.
- Every gateway and manifest fetch goes through `netguard.public_transport()`; never add an outbound path that
    bypasses it.
- Error statuses follow the C2PA OpenAPI: invalid queries are `400`, unknown manifests are `404` (never `400`).
- The C2PA specification repository (specs-core) is private. The only content copied from it is the CC BY 4.0
    OpenAPI subset in `c2pa-sbr.json` (approved by Titusz). Do not copy other specification text into this public
    repository; link to spec.c2pa.org instead.
- Commits follow Conventional Commits (`feat:`, `fix:`, `build:`, `docs:`, `ci:`).
