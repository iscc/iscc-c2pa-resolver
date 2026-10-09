---
title: API reference
description: Routes of the ISCC C2PA Resolver, a C2PA Soft Binding Resolution API for io.iscc.v0
icon: lucide/braces
---

# API reference

!!! warning "Experimental"

    This beta MVP and its documentation are provided as is, without warranty of any kind. The API, the gateway contract and the public instances may change in breaking ways before version 1.0.

The resolver implements the query, fetch and service routes of the
[C2PA Soft Binding Resolution API](https://spec.c2pa.org/specifications/specifications/2.4/softbinding/Decoupled.html)
for one algorithm, `io.iscc.v0`. It follows the API of the upcoming C2PA specification 2.5, which adds the service
routes; the OpenAPI document of that draft still carries version `2.4.0`. Access is open; no token is required.
Every instance serves an interactive API reference at `/docs` and the OpenAPI document at `/openapi/openapi.json`
(also as `/openapi/openapi.yaml`). Schemas named `c2pa.*` in that document are copied verbatim from the C2PA
OpenAPI definition (`/openapi/c2pa-sbr.json`, CC BY 4.0).
{ .iscc-lead }

## Routes

| Route                                           | Behaviour                                                                    |
| ----------------------------------------------- | ---------------------------------------------------------------------------- |
| `GET /v1/matches/byBinding`                     | Query with `alg`, `value` and optional `maxResults`                          |
| `POST /v1/matches/byBinding`                    | Same query with `{"alg": ..., "value": ...}` as JSON body                    |
| `GET /v1/iscc/{isccId}/manifests/{manifestId}`  | The manifest of a match, as linked by its `endpoint`                         |
| `GET /v1/manifests/{manifestId}`                | The manifest of a match of the last hour, for clients that ignore `endpoint` |
| `GET /v1/services/supportedAlgorithms`          | `{"watermarks": [], "fingerprints": [{"alg": "io.iscc.v0"}]}`                |
| `GET /v1/services/capabilities`                 | `info.version` of `c2pa-sbr.json` (`2.4.0`), no optional capabilities        |
| `GET /v1/services/status`                       | `ok`, or `degraded` while the search backend is not ready                    |
| `GET /.well-known/c2pa-soft-binding-resolution` | Absolute URLs of the API base, capability and status routes                  |

Not implemented: `byContent`, `byReference`, and the store, bindings and receipt routes.

## Query by binding

The `value` is the base64 encoding of an ISCC-SEQ, as specified in
[IEP-0020](https://ieps.iscc.codes/iep-0020/). Percent-encode it in a `GET` request.

```python
import base64
from urllib.parse import quote

import iscc_core as ic

units = [
    "ISCC:EAD2RASIYU5IKLENP2OFI4CHZGRWYQCSW2WKX3Y6FJGOCXSYNYGLGBI",  # Content-Code
    "ISCC:GADQLNA7GRZESMRF2J7NZPNWGI3II2ST5YUN5SS6GVQ2ZQGJXPPYDNI",  # Data-Code
    "ISCC:IAD2KIVPJIWJZP3KQCESJL6SVT5APEZUPOJWM6HVTAXCF7OT3VFA4NY",  # Instance-Code
]
value = quote(base64.b64encode(ic.encode_seq(units)).decode("ascii"), safe="")
url = f"https://c2pa-test.iscc.io/v1/matches/byBinding?alg=io.iscc.v0&value={value}&maxResults=5"
```

Response:

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

| Field             | Meaning                                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------------------- |
| `manifestId`      | Manifest ID, as in the gateway URL of the declaration                                                 |
| `endpoint`        | Fetch the manifest from `GET {endpoint}/manifests/{manifestId}`                                       |
| `similarityScore` | 100 for an equal Instance-Code, else best unit similarity ([score](how-it-works.md#similarity-score)) |
| `isccId`          | Extension: ISCC-ID of the declaration behind the match                                                |

`maxResults` defaults to 10. Values above 100 are capped at 100.

## Fetch a manifest

```bash
curl -o manifest.c2pa \
    "https://c2pa-test.iscc.io/v1/iscc/maigkv5faaxoxyab/manifests/urn:c2pa:F9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4"
```

The answer is the C2PA Manifest Store as `application/c2pa`, passed through unchanged from the gateway URL of the
declaration ([how it works](how-it-works.md#fetching-the-manifest)).

## Errors

| Status | When                                                                                         |
| ------ | -------------------------------------------------------------------------------------------- |
| `400`  | Unsupported `alg`, invalid base64 or ISCC-SEQ, no usable unit, missing or invalid parameter  |
| `404`  | Unknown manifest ID, or the declaration does not point to a C2PA Manifest Store with that ID |
| `411`  | Request body sent without `Content-Length`                                                   |
| `405`  | Method not supported for the route; `Allow` lists the supported methods                      |
| `413`  | Request body larger than 16 KB                                                               |
| `500`  | The search backend or the manifest repository is unavailable                                 |

Error bodies are JSON with a `detail` member.
