---
title: Operations
description: Configure, run and deploy the ISCC C2PA Resolver
icon: lucide/server
---

# Operations

The resolver ships as a container image. One image serves mainnet and testnet; environment variables select the
search backend.
{ .iscc-lead }

## Run

```bash
docker run -d --name c2pa-resolver -p 8000:8000 \
    -e ISCC_C2PA_RESOLVER_SEARCH_URL=https://search-test.iscc.id \
    -e ISCC_C2PA_RESOLVER_SEARCH_INDEX=idptest \
    ghcr.io/iscc/iscc-c2pa-resolver:main
```

The image runs as an unprivileged user, listens on port 8000, keeps no data on disk, and reports container health
from `GET /healthz`.

## Scaling

One process handles the load: the resolver is I/O bound and asynchronous. Replicas work, with one limit. The
fallback route `GET /v1/manifests/{manifestId}` only knows the manifest IDs that the same process returned, so
behind a load balancer it may answer `404` for a recent match. The `endpoint` route of a match keeps no state and
works on every replica. Use sticky sessions if clients that ignore `endpoint` matter.

## Configuration

| Variable                                 | Default                  | Meaning                                                                                     |
| ---------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------- |
| `ISCC_C2PA_RESOLVER_SEARCH_URL`          | `https://search.iscc.io` | iscc-search aggregator                                                                      |
| `ISCC_C2PA_RESOLVER_SEARCH_INDEX`        | `idp`                    | Aggregator index: `idp` mainnet, `idptest` testnet; also the network the landing page names |
| `ISCC_C2PA_RESOLVER_SEARCH_TIMEOUT`      | `5.0`                    | Seconds per aggregator request                                                              |
| `ISCC_C2PA_RESOLVER_GATEWAY_TIMEOUT`     | `3.0`                    | Seconds per manifest address check, in total                                                |
| `ISCC_C2PA_RESOLVER_GATEWAY_CONCURRENCY` | `16`                     | Parallel checks and manifest fetches per process                                            |
| `ISCC_C2PA_RESOLVER_MANIFEST_TIMEOUT`    | `10.0`                   | Seconds per manifest fetch, in total                                                        |
| `FORWARDED_ALLOW_IPS`                    | `127.0.0.1`              | Proxies whose `X-Forwarded-*` headers uvicorn trusts                                        |

## Behind a reverse proxy

Terminate TLS at the proxy and forward to port 8000. Set `FORWARDED_ALLOW_IPS` to the proxy's address. The
discovery document advertises absolute URLs built from the request, so without trusted `X-Forwarded-Proto` and
`X-Forwarded-Host` headers it would announce `http://` addresses. Access logs then also show client addresses. The
resolver does not rate-limit; apply per-client limits at the proxy.

The file check on the landing page loads a WebAssembly module that the app stores precompressed and sends with
`Content-Encoding: br` or `gzip`; the proxy should pass it through unchanged. After the visitor confirms, the
browser uploads a file to `https://web.iscc.io` to compute its ISCC. A Content Security Policy at the proxy must
allow that origin in `connect-src`, `blob:` in `worker-src` and `img-src`, and `'wasm-unsafe-eval'` in
`script-src`.

## Images and releases

| Tag                 | Published when                                                                   |
| ------------------- | -------------------------------------------------------------------------------- |
| `main`, `sha-<sha>` | Every push to `main` that passes all checks                                      |
| `X.Y.Z`, `latest`   | A GitHub Release `vX.Y.Z`; the tested `sha-<sha>` image is retagged, not rebuilt |

Testnet follows `main`. Mainnet runs a release tag.

## Security

- Manifest addresses come from anyone who declares. Every such connection goes to a checked public address only
    (no loopback, private, link-local, shared or multicast ranges, also not through IPv4-mapped IPv6 or redirects).
    Gateway URLs that are not manifest addresses are never fetched.
- Address checks read 32 bytes within 3 seconds, manifest fetches at most 10 MB within 10 seconds; parallel
    fetches are bounded. Only bytes that start like a C2PA Manifest Store are passed through.
- Request bodies are capped at 16 KB. Query values are capped at 4096 characters.
