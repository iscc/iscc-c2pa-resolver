# ISCC C2PA Resolver

[![CI](https://github.com/iscc/iscc-c2pa-resolver/actions/workflows/ci.yml/badge.svg)](https://github.com/iscc/iscc-c2pa-resolver/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

> [!WARNING]
> **Experimental (beta, MVP).** This service and its documentation are provided as is, without warranty of any kind. The API, the gateway contract and the public instances may change in breaking ways before version 1.0.

A [C2PA Soft Binding Resolution API](https://spec.c2pa.org/specifications/specifications/2.4/softbinding/Decoupled.html)
for the ISCC fingerprint `io.iscc.v0`. It recovers lost C2PA Manifests by content: a client sends the ISCC of a
file that lost its Content Credentials, the resolver searches the ISCC Discovery Protocol for declarations of
similar content whose gateway URL is the address of a C2PA Manifest, and returns those manifests as matches.
It follows the Soft Binding Resolution API of the upcoming C2PA specification 2.5, whose OpenAPI document still
carries version 2.4.0.

| Network | Resolver                    | Search backend                |
| ------- | --------------------------- | ----------------------------- |
| Mainnet | `https://c2pa.iscc.io`      | `https://search.iscc.io`      |
| Testnet | `https://c2pa-test.iscc.io` | `https://search-test.iscc.id` |

## Quick look

```bash
curl "https://c2pa-test.iscc.io/v1/matches/byBinding?alg=io.iscc.v0&value=<percent-encoded base64 ISCC-SEQ>"
```

```json
{
  "matches": [
    {
      "manifestId": "urn:c2pa:F9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4",
      "endpoint": "https://c2pa-test.iscc.io/v1/iscc/maigkv5faaxoxyab",
      "similarityScore": 93,
      "isccId": "ISCC:MAIGKV5FAAXOXYAB"
    }
  ]
}
```

Then `GET {endpoint}/manifests/{manifestId}` returns the C2PA Manifest Store.

## What it implements

- `GET` and `POST /v1/matches/byBinding` for `io.iscc.v0`, with values as specified in
    [IEP-0020](https://ieps.iscc.codes/iep-0020/)
- `GET /v1/iscc/{isccId}/manifests/{manifestId}`: the manifest of a match, linked by its `endpoint`, without
    stored state
- `GET /v1/manifests/{manifestId}`: the same for clients that ignore `endpoint`, for matches of the last hour
- `GET /v1/services/supportedAlgorithms`, `/capabilities`, `/status` and
    `/.well-known/c2pa-soft-binding-resolution`
- Open access, no store or bindings routes; it passes the C2PA Soft Binding API conformance harness

Repositories become findable by declaring their assets with the manifest address on their Soft Binding Resolution
API (`https://…/manifests/{manifestId}`) as gateway URL. See the [documentation](https://c2pa-resolver.iscc.codes/) for the API, the gateway contract,
scoring, operations and development.

## Development

```bash
uv sync
uv run prek install
uv run poe all      # format, type check, test with 100% coverage
uv run poe serve    # http://127.0.0.1:45460
```

## License

Apache-2.0. Documentation CC BY 4.0.
