---
title: ISCC C2PA Resolver
description: C2PA Soft Binding Resolution API for the ISCC fingerprint io.iscc.v0, built on the ISCC Discovery Protocol
icon: lucide/house
hide:
  - toc
---

# ISCC C2PA Resolver

!!! warning "Experimental"

    This beta MVP and its documentation are provided as is, without warranty of any kind. The API, the gateway contract and the public instances may change in breaking ways before version 1.0.

## Recover lost Content Credentials by content

A C2PA Manifest gets lost when a platform strips metadata or re-encodes a file. The ISCC C2PA Resolver finds it
again: it answers C2PA soft binding queries for the fingerprint algorithm `io.iscc.v0` by searching the ISCC
Discovery Protocol, and points the client to the manifest.
{ .iscc-lead }

[How it works](how-it-works.md){ .iscc-btn .iscc-btn--primary }
[API reference](api.md){ .iscc-btn }

*Open source · C2PA Soft Binding Resolution API, upcoming C2PA 2.5 · maintained by the [ISCC Foundation](https://iscc.io)*

## Find your path

<div class="iscc-cards" markdown>

<div class="iscc-card" markdown>

### C2PA clients

Compute the ISCC of a file without credentials, query the resolver and fetch the matching manifest.

[Query the API →](api.md)

</div>

<div class="iscc-card" markdown>

### Manifest repositories

Declare your assets with their manifest addresses, and every resolver on the network finds your manifests.

[Become findable →](gateway.md)

</div>

<div class="iscc-card" markdown>

### Operators

Run your own resolver for mainnet or testnet from the container image.

[Deploy →](operations.md)

</div>

</div>

## Public instances

| Network | Resolver                    | Search backend                |
| ------- | --------------------------- | ----------------------------- |
| Mainnet | `https://c2pa.iscc.io`      | `https://search.iscc.io`      |
| Testnet | `https://c2pa-test.iscc.io` | `https://search-test.iscc.id` |

Discovery document: `GET /.well-known/c2pa-soft-binding-resolution` on either host.
