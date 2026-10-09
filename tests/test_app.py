"""End-to-end tests of the HTTP API with a fake aggregator and fake manifest repositories."""

import base64
import hashlib
import json
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from iscc_c2pa_resolver import __version__
from iscc_c2pa_resolver import app as app_module
from iscc_c2pa_resolver.binding import MAX_VALUE_LENGTH
from iscc_c2pa_resolver.settings import Settings
from tests.conftest import (
    ID_EARLY,
    ID_LATE,
    MANIFEST,
    MANIFEST_ID,
    MANIFEST_URL,
    META,
    SEARCH_PATH,
    SEARCH_URL,
    VECTOR_1,
    encode,
    hit,
)

ENDPOINT = "http://testserver/v1/iscc/maigkv5faaxoxyab"
VENDOR = app_module.STATIC / "vendor"
MATCH = {"manifestId": MANIFEST_ID, "endpoint": ENDPOINT, "similarityScore": 93, "isccId": ID_EARLY}
MIRROR_URL = f"https://other.example/v1/manifests/{MANIFEST_ID}"


def query(client, value, alg="io.iscc.v0", **params):
    """GET /v1/matches/byBinding with a percent-encoded value."""
    extra = "".join(f"&{key}={val}" for key, val in params.items())
    return client.get(f"/v1/matches/byBinding?alg={alg}&value={quote(value, safe='')}{extra}")


def publish(net, iscc_id=ID_EARLY, manifest_url=MANIFEST_URL):
    """Let a declaration whose gateway URL is a manifest address, and its manifest, exist on the fake network."""
    net.hits(hit(iscc_id, manifest_url, CONTENT_TEXT_V0=0.93))
    net.declaration(iscc_id, manifest_url)
    net.manifest(manifest_url)


def test_query_by_binding_and_fetch_manifest_from_endpoint(client, net):
    publish(net)
    response = query(client, VECTOR_1)
    assert response.status_code == 200
    assert response.json() == {"matches": [MATCH]}
    manifest = client.get(f"{ENDPOINT}/manifests/{MANIFEST_ID}")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"] == "application/c2pa"
    assert manifest.content == MANIFEST
    assert [r.headers["accept"] for r in net.sent_to(MANIFEST_URL)] == ["application/c2pa"] * 2  # probe and fetch


def test_endpoint_needs_no_prior_query(client, net):
    publish(net)
    assert client.get(f"/v1/iscc/MAIGKV5FAAXOXYAB/manifests/{MANIFEST_ID}").content == MANIFEST


def test_fetch_recent_manifest_without_endpoint(client, net):
    publish(net)
    assert client.get(f"/v1/manifests/{MANIFEST_ID}").status_code == 404
    query(client, VECTOR_1)
    manifest = client.get(f"/v1/manifests/{MANIFEST_ID}?returnActiveManifest=true")
    assert manifest.status_code == 200
    assert manifest.content == MANIFEST


def test_recent_manifest_keeps_first_declaration(client, net):
    publish(net)
    query(client, VECTOR_1)
    publish(net, ID_LATE, MIRROR_URL)
    query(client, VECTOR_1)
    probes = len(net.sent_to(MIRROR_URL))
    client.get(f"/v1/manifests/{MANIFEST_ID}")
    assert len(net.sent_to(MANIFEST_URL)) == 2  # probe and fetch
    assert len(net.sent_to(MIRROR_URL)) == probes


@pytest.mark.parametrize("encode_slash", [False, True])
def test_fetch_manifest_with_slash_in_id(client, net, encode_slash):
    manifest_id = f"{MANIFEST_ID}:acme/x"  # the claim generator part of a C2PA URN may contain "/"
    publish(net, manifest_url=f"https://repo.example/v1/manifests/{manifest_id}")
    assert query(client, VECTOR_1).json()["matches"][0]["manifestId"] == manifest_id
    path = quote(manifest_id, safe=":" if encode_slash else ":/")
    assert client.get(f"{ENDPOINT}/manifests/{path}").content == MANIFEST
    assert client.get(f"/v1/manifests/{path}").content == MANIFEST


@pytest.mark.parametrize(
    "path",
    [
        f"/v1/iscc/maigkv5faaxoxyab/manifests/{MANIFEST_ID}:other",  # the gateway URL names another manifest
        f"/v1/iscc/maigkv5n6ntvf4ab/manifests/{MANIFEST_ID}",  # unknown declaration
        f"/v1/iscc/maigkv5faaxoxya/manifests/{MANIFEST_ID}",  # not an ISCC-ID
        f"/v1/iscc/xxxxxxxxxxxxxxxx/manifests/{MANIFEST_ID}",  # base32, but not an ISCC-ID
    ],
)
def test_fetch_unknown_manifest_is_404(client, net, path):
    publish(net)
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "C2PA Manifest not found"}


def test_fetch_manifest_that_is_not_a_manifest_store_is_404(client, net):
    publish(net)
    net.content(MANIFEST_URL, b"<html>not a manifest</html>")
    assert client.get(f"{ENDPOINT}/manifests/{MANIFEST_ID}").status_code == 404


def test_fetch_manifest_reports_repository_failure(client, net):
    publish(net)
    net.content(MANIFEST_URL, b"", status=503)
    response = client.get(f"{ENDPOINT}/manifests/{MANIFEST_ID}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Manifest repository unavailable"}


def test_fetch_manifest_reports_backend_failure(client, net):
    publish(net)
    net.json(f"{SEARCH_URL}/indexes/idp/assets/{ID_EARLY}", {}, status=502)
    response = client.get(f"{ENDPOINT}/manifests/{MANIFEST_ID}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Search backend unavailable"}


def test_query_by_large_binding(client, net):
    publish(net)
    response = client.post("/v1/matches/byBinding", json={"alg": "io.iscc.v0", "value": VECTOR_1})
    assert response.status_code == 200
    assert response.json() == {"matches": [MATCH]}


def test_query_sends_searchable_units_only(client, net):
    net.hits()
    query(client, VECTOR_1)
    body = json.loads(net.sent_to(SEARCH_URL + SEARCH_PATH)[0].read())
    assert META not in body["units"]
    assert len(body["units"]) == 3


def test_query_caps_max_results(client, net):
    net.hits()
    assert query(client, VECTOR_1, maxResults=5000).status_code == 200
    assert net.sent_to(SEARCH_URL + SEARCH_PATH)[0].url.params["limit"] == "100"


@pytest.mark.parametrize(
    ("value", "alg", "params"),
    [
        (VECTOR_1, "io.iscc.v1", {}),  # unsupported algorithm
        ("!!!", "io.iscc.v0", {}),  # not base64
        (encode([META]), "io.iscc.v0", {}),  # nothing searchable
        (VECTOR_1, "io.iscc.v0", {"maxResults": 0}),
        (VECTOR_1, "io.iscc.v0", {"maxResults": "ten"}),
        ("A" * (MAX_VALUE_LENGTH + 1), "io.iscc.v0", {}),
    ],
)
def test_query_rejects_invalid_input_with_400(client, value, alg, params):
    response = query(client, value, alg=alg, **params)
    assert response.status_code == 400
    assert "detail" in response.json()


def test_query_requires_value(client):
    response = client.get("/v1/matches/byBinding?alg=io.iscc.v0")
    assert response.status_code == 400
    assert response.json() == {"detail": [{"loc": ["query", "value"], "msg": "Field required"}]}


@pytest.mark.parametrize("body", [{"alg": "io.iscc.v0"}, {"value": VECTOR_1}, [], {"alg": 1, "value": VECTOR_1}])
def test_query_by_large_binding_rejects_invalid_body(client, body):
    assert client.post("/v1/matches/byBinding", json=body).status_code == 400


def test_query_reports_backend_failure(client, net):
    net.json(SEARCH_URL + SEARCH_PATH, {}, status=502)
    response = query(client, VECTOR_1)
    assert response.status_code == 500
    assert response.json() == {"detail": "Search backend unavailable"}


def test_unknown_manifest_is_404(client):
    response = client.get("/v1/manifests/urn:c2pa:conformance-does-not-exist", headers={"Authorization": "Bearer x"})
    assert response.status_code == 404


def test_supported_algorithms(client):
    assert client.get("/v1/services/supportedAlgorithms").json() == {
        "watermarks": [],
        "fingerprints": [{"alg": "io.iscc.v0"}],
    }


def test_capabilities_and_discovery_agree(client):
    capabilities = client.get("/v1/services/capabilities").json()
    discovery = client.get("/.well-known/c2pa-soft-binding-resolution").json()
    c2pa_version = client.get("/openapi/c2pa-sbr.json").json()["info"]["version"]
    assert capabilities == {"c2paSpecificationVersion": c2pa_version, "supportedCapabilities": []}
    assert discovery == {
        "apiEndpoint": "http://testserver/v1",
        "c2paSpecificationVersion": capabilities["c2paSpecificationVersion"],
        "capabilitiesEndpoint": "http://testserver/v1/services/capabilities",
        "statusEndpoint": "http://testserver/v1/services/status",
    }


def test_status(client, net):
    status = client.get("/v1/services/status").json()
    assert status["status"] == "ok"
    assert status["timestamp"].endswith("Z")


def test_status_degraded(client, net):
    net.json(SEARCH_URL + "/readyz", {}, status=503)
    assert client.get("/v1/services/status").json()["status"] == "degraded"


def test_root_serves_json_to_api_clients(client):
    response = client.get("/", headers={"Accept": "application/json"})
    summary = response.json()
    assert summary["discovery"] == "/.well-known/c2pa-soft-binding-resolution"
    assert summary["version"] == __version__
    assert summary["network"] == "mainnet"
    assert "Accept" in response.headers["vary"]


def test_root_serves_landing_page_to_browsers(client):
    response = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert response.headers["content-type"].startswith("text/html")
    assert 'href="/docs"' in response.text
    assert "Accept" in response.headers["vary"]


def test_network_of_index():
    assert app_module.network_of("idp") == "mainnet"
    assert app_module.network_of("idptest") == "testnet"
    assert app_module.network_of("myindex") == "myindex"


def test_landing_page_shows_network_and_version(client):
    html = client.get("/", headers={"Accept": "text/html"}).text
    assert '<body data-network="mainnet">' in html
    assert '<span class="network">mainnet</span>' in html
    assert f'<span class="version">v{__version__}</span>' in html
    assert "{{" not in html


@pytest.mark.parametrize(
    ("index", "shown"),
    [("idptest", "testnet"), ('x"<y', "x&quot;&lt;y")],
)
def test_landing_page_names_other_networks(net, index, shown):
    with TestClient(
        app_module.create_app(Settings(search_url=SEARCH_URL, search_index=index), net.transport)
    ) as client:
        html = client.get("/", headers={"Accept": "text/html"}).text
    assert f'<body data-network="{shown}">' in html
    assert f'<span class="network">{shown}</span>' in html


def test_static_assets_are_same_origin(client):
    for page in ("/", "/docs"):
        html = client.get(page, headers={"Accept": "text/html"}).text
        for ref in re.findall(r'(?:src|href)="(/[^"]+)"', html):
            assert client.get(ref).status_code == 200, ref
        assert "https://unpkg.com" not in html
        assert "cdn.jsdelivr" not in html


def import_map(client):
    """The import map of the landing page."""
    html = client.get("/", headers={"Accept": "text/html"}).text
    return json.loads(re.search(r'<script type="importmap">(.*?)</script>', html).group(1))["imports"]


def check_script_constant(client, name):
    """A string constant of the file check script."""
    return re.search(rf'const {name} = "([^"]+)"', client.get("/static/check.js").text).group(1)


def wasm_integrity():
    """The SHA-512 digest that the vendored c2pa-web requires of its WebAssembly module (subresource integrity)."""
    (chunk,) = VENDOR.glob("c2pa-web-*/c2pa-*.js")
    return base64.b64decode(re.search(r'"sha512-([A-Za-z0-9+/=]+)"', chunk.read_text(encoding="utf-8")).group(1))


def test_import_map_targets_exist(client):
    for target in import_map(client).values():
        assert client.get(target).status_code == 200, target


def test_vendored_c2pa_web_imports_resolve(client):
    imports = import_map(client)
    for module in VENDOR.glob("c2pa-web-*/*.js"):
        for specifier in re.findall(r'^import .*? from "([^"]+)";', module.read_text(encoding="utf-8"), re.MULTILINE):
            assert (module.parent / specifier).is_file() if specifier.startswith("./") else specifier in imports


def test_file_check_loads_vendored_assets(client):
    c2pa_web = check_script_constant(client, "C2PA_WEB")
    assert client.get(c2pa_web + "index.js").headers["content-type"].startswith("text/javascript")
    assert client.get(c2pa_web + "c2pa_bg.wasm", headers={"Accept-Encoding": "br"}).status_code == 200
    assert "-----BEGIN CERTIFICATE-----" in client.get(check_script_constant(client, "TRUST_LIST")).text


@pytest.mark.parametrize(
    ("accept", "coding"),
    [
        ("gzip, deflate, br, zstd", "br"),
        ("gzip, deflate", "gzip"),
        ("br;q=0, gzip;q=1", "gzip"),
        ("BR ; Q = 0.000, GZIP;q=0.5", "gzip"),
        ("*", "br"),
        ("*, br;q=0", "gzip"),
    ],
)
def test_wasm_is_served_precompressed(client, accept, coding):
    response = client.get(
        check_script_constant(client, "C2PA_WEB") + "c2pa_bg.wasm", headers={"Accept-Encoding": accept}
    )
    assert response.headers["content-encoding"] == coding
    assert response.headers["content-type"] == "application/wasm"
    assert "Accept-Encoding" in response.headers["vary"]
    assert hashlib.sha512(response.content).digest() == wasm_integrity()


@pytest.mark.parametrize("accept", ["identity", "br;q=0, gzip;q=0.", "*;q=0"])
def test_wasm_needs_an_accepted_coding(client, accept):
    url = check_script_constant(client, "C2PA_WEB") + "c2pa_bg.wasm"
    assert client.get(url, headers={"Accept-Encoding": accept}).status_code == 404


def test_stylesheet_keeps_hidden_elements_hidden(client):
    # The upload offer is hidden while a file is checked; a display rule must not make its button clickable
    css = client.get("/static/site.css").text
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", css)


def test_precompressed_files_answer_conditional_requests(client):
    url = check_script_constant(client, "C2PA_WEB") + "c2pa_bg.wasm"
    first = client.get(url, headers={"Accept-Encoding": "br"})
    again = client.get(url, headers={"Accept-Encoding": "br", "If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_static_files_are_revalidated_before_reuse(client):
    response = client.get("/static/check.js")
    assert response.headers["cache-control"] == "no-cache"
    assert "content-encoding" not in response.headers
    assert client.get("/static/check.js", headers={"If-None-Match": response.headers["etag"]}).status_code == 304


def test_static_files_refuse_other_methods(client):
    assert client.post("/static/check.js").status_code == 405


def test_docs_page_loads_spec(client):
    assert 'apiDescriptionUrl="/openapi/openapi.json"' in client.get("/docs").text


def test_landing_page_links_viewable_spec(client):
    assert 'href="/openapi/openapi.json"' in client.get("/", headers={"Accept": "text/html"}).text


def test_openapi_files_are_served(client):
    spec = client.get("/openapi/openapi.json")
    assert spec.headers["content-type"] == "application/json"
    assert spec.json()["openapi"].startswith("3.1")
    yaml_spec = client.get("/openapi/openapi.yaml")
    assert yaml_spec.headers["content-type"].startswith("application/yaml")
    assert yaml_spec.text.startswith("openapi: 3.1")
    assert "/matches/byBinding" in client.get("/openapi/c2pa-sbr.json").json()["paths"]


def test_fastapi_generated_docs_are_disabled(client):
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_cors(client):
    response = client.get("/v1/services/status", headers={"Origin": "https://app.example"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_body_too_large(client):
    body = json.dumps({"alg": "io.iscc.v0", "value": "A" * app_module.MAX_BODY_BYTES})
    response = client.post("/v1/matches/byBinding", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_body_with_invalid_length(client):
    response = client.post("/v1/matches/byBinding", content=b"{}", headers={"Content-Length": "abc"})
    assert response.status_code == 413


def test_chunked_body_refused(client):
    response = client.post("/v1/matches/byBinding", content=iter([b"{}"]), headers={"Content-Type": "application/json"})
    assert response.status_code == 411


@pytest.mark.parametrize(
    ("method", "path", "allow"),
    [("PUT", "/v1/matches/byBinding", "GET, HEAD, POST"), ("DELETE", "/v1/services/status", "GET, HEAD")],
)
def test_method_not_allowed_lists_all_methods(client, method, path, allow):
    response = client.request(method, path)
    assert response.status_code == 405
    assert response.headers["allow"] == allow


def assert_head_like_get(client, path):
    """HEAD answers with the status and headers of GET, without content."""
    get = client.get(path)
    head = client.head(path)
    assert head.status_code == get.status_code == 200
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.headers["content-length"] == get.headers["content-length"]
    assert head.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/docs",
        "/healthz",
        "/.well-known/c2pa-soft-binding-resolution",
        "/v1/services/supportedAlgorithms",
        "/v1/services/capabilities",
        "/v1/services/status",
    ],
)
def test_head_on_service_routes(client, path):
    assert_head_like_get(client, path)


def test_head_on_query_and_manifest(client, net):
    publish(net)
    assert_head_like_get(client, f"/v1/matches/byBinding?alg=io.iscc.v0&value={quote(VECTOR_1, safe='')}")
    assert_head_like_get(client, f"{ENDPOINT}/manifests/{MANIFEST_ID}")
    assert_head_like_get(client, f"/v1/manifests/{MANIFEST_ID}")
