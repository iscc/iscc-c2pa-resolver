"""Tests that the hand-written spec is valid, matches the app, and stays conformant with the C2PA OpenAPI subset.

`c2pa-sbr.json` is the verbatim subset of the C2PA Soft Binding Resolution API that `scripts/sync_c2pa_openapi.py`
copies from the specification repository. Every operation in it must exist in `openapi.yaml` with the same
operationId, every upstream parameter must be accepted the same way, and every upstream response schema must be
referenced unchanged, except for the deviations listed here.
"""

import json
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate

import iscc_c2pa_resolver
from iscc_c2pa_resolver.app import create_app

SPEC_DIR = Path(iscc_c2pa_resolver.__file__).parent / "openapi"
OURS = yaml.safe_load((SPEC_DIR / "openapi.yaml").read_text(encoding="utf-8"))
UPSTREAM = json.loads((SPEC_DIR / "c2pa-sbr.json").read_text(encoding="utf-8"))
UPSTREAM_REF = "c2pa-sbr.json#/components/schemas/"

# Documented deviations from the upstream operations, by (path, method).
EXTENDED_RESULTS = {"c2pa.softBindingQueryResult": "#/components/responses/QueryResult"}  # adds `isccId`

UPSTREAM_OPERATIONS = [
    (path, method) for path, item in UPSTREAM["paths"].items() for method in item if method in ("get", "post")
]


def our_path(path):
    # type: (str) -> str
    """Map an upstream path (relative to the /v1 server URL) to the resolver's absolute path."""
    return path if path.startswith("/.well-known/") else f"/v1{path}"


def resolve(spec, item):
    # type: (dict, dict) -> dict
    """Follow a local `$ref` such as `#/components/responses/X`."""
    if "$ref" not in item:
        return item
    node = spec
    for part in item["$ref"].removeprefix("#/").split("/"):
        node = node[part]
    return node


def json_schema_ref(response):
    # type: (dict) -> str | None
    """The `$ref` of a response's application/json schema, if any."""
    return response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")


def test_json_spec_is_current():
    assert json.loads((SPEC_DIR / "openapi.json").read_text(encoding="utf-8")) == OURS


@pytest.mark.parametrize("name", ["openapi.yaml", "openapi.json", "c2pa-sbr.json"])
def test_spec_files_are_valid(name):
    path = SPEC_DIR / name
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate(spec, base_uri=path.as_uri())


def test_upstream_subset_is_attributed():
    assert UPSTREAM["info"]["license"]["name"] == "Creative Commons Attribution 4.0 International"
    assert UPSTREAM["info"]["x-source"].startswith("https://github.com/c2pa-org/specs-core/blob/")


def test_spec_implements_c2pa_specification_version():
    from iscc_c2pa_resolver.app import SPEC_VERSION

    assert UPSTREAM["info"]["version"] == SPEC_VERSION


@pytest.mark.parametrize(("path", "method"), UPSTREAM_OPERATIONS)
def test_operation_is_implemented_with_upstream_operation_id(path, method):
    upstream = UPSTREAM["paths"][path][method]
    ours = OURS["paths"][our_path(path)][method]
    assert ours["operationId"] == upstream["operationId"]


@pytest.mark.parametrize(("path", "method"), UPSTREAM_OPERATIONS)
def test_upstream_parameters_are_accepted(path, method):
    ours = OURS["paths"][our_path(path)][method].get("parameters", [])
    ours_by_key = {(p["name"], p["in"]): p for p in (resolve(OURS, p) for p in ours)}
    for parameter in UPSTREAM["paths"][path][method].get("parameters", []):
        mine = ours_by_key[(parameter["name"], parameter["in"])]
        assert mine.get("required", False) == parameter.get("required", False), parameter["name"]


@pytest.mark.parametrize(("path", "method"), UPSTREAM_OPERATIONS)
def test_upstream_success_schema_is_used_unchanged(path, method):
    upstream = UPSTREAM["paths"][path][method]["responses"]["200"]
    ours = OURS["paths"][our_path(path)][method]["responses"]["200"]
    upstream_ref = json_schema_ref(upstream)
    if upstream_ref is None:  # not JSON, such as the application/c2pa manifest store
        assert resolve(OURS, ours)["content"] == upstream["content"]
        return
    name = upstream_ref.rsplit("/", 1)[1]
    if name in EXTENDED_RESULTS:
        assert ours["$ref"] == EXTENDED_RESULTS[name]
    else:
        assert json_schema_ref(ours) == UPSTREAM_REF + name


def test_query_body_is_upstream_schema():
    body = OURS["paths"]["/v1/matches/byBinding"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"] == UPSTREAM_REF + "c2pa.softBindingQuery"


def test_match_extends_upstream_match():
    parts = OURS["components"]["schemas"]["IsccMatch"]["allOf"]
    assert parts[0]["$ref"] == UPSTREAM_REF + "c2pa.softBindingQueryResult/properties/matches/items"


def test_spec_paths_match_app_routes(settings):
    app = create_app(settings)
    documented = {(path, method.upper()) for path, item in OURS["paths"].items() for method in item}
    routed = {
        (route.path_format, method)  # path without converters such as ":path"
        for route in app.routes
        for method in getattr(route, "methods", None) or ()
        if route.path.startswith(("/v1/", "/.well-known/")) and method != "HEAD"  # implied by GET (RFC 9110)
    }
    assert documented == routed
