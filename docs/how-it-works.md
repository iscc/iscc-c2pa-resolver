---
title: How it works
description: How the resolver turns an io.iscc.v0 soft binding into C2PA manifest matches
icon: lucide/workflow
---

# How it works

The resolver is a stateless search client of the ISCC Discovery Protocol (IDP). It never writes declarations and
stores nothing between requests apart from caches.
{ .iscc-lead }

## Roles

- **ISCC hubs** record signed declarations of ISCC codes in transparency logs. Each declaration carries a gateway
    URL.
- **The iscc-search aggregator** indexes the declarations of all hubs of one network for similarity search.
- **Gateway URLs** say where more information about a declaration lives. A gateway URL that is the address of a
    C2PA Manifest on a Soft Binding Resolution API makes its declaration a match candidate
    ([gateway contract](gateway.md)).
- **The resolver** speaks the C2PA Soft Binding Resolution API on top of that.

## Query pipeline

1. **Decode.** Only `alg=io.iscc.v0` is accepted. The value is decoded leniently (padded or unpadded, standard or
    URL-safe base64) into an ISCC-SEQ as defined by [IEP-0020](https://ieps.iscc.codes/iep-0020/). Units that are
    not Version 0 with a 256-bit body are ignored, as IEP-0020 requires. Meta-Codes are ignored too: equal
    metadata says nothing about equal content. A value without any usable Semantic-, Content-, Data- or
    Instance-Code is rejected with `400`.
1. **Search.** The units go to the aggregator, asking for five times `maxResults` candidates (at most 100),
    because most declarations in the network carry no manifest.
1. **Check manifest addresses.** Hits whose gateway URL is not of the form `https://…/manifests/{manifestId}` are
    dropped without a request. For the others the resolver reads the first 32 bytes and checks that they start a
    C2PA Manifest Store. Results are cached: a day for a manifest store, one hour for an address that served
    something else or nothing, one minute for one that failed or timed out.
1. **Rank.** Hits that point to the same manifest address are merged, keeping the best. Matches are sorted by
    score; among equal scores the earlier declaration comes first.
1. **Answer.** Each match carries the manifest ID from the gateway URL and an `endpoint` on the resolver
    that names the declaration: `https://c2pa.iscc.io/v1/iscc/{iscc-id}`, with the ISCC-ID in lowercase and
    without the `ISCC:` prefix.

## Similarity score

C2PA defines `similarityScore` as an integer from 0 to 100 whose meaning each algorithm defines. For `io.iscc.v0`:

| Match                                                  | Score                                 |
| ------------------------------------------------------ | ------------------------------------- |
| Equal Instance-Code: the same bytes as the source      | 100                                   |
| Otherwise, best Semantic-, Content- or Data-Code match | 100 × similarity, rounded, at most 99 |
| Meta-Code                                              | never contributes                     |

A score is a shortlisting signal, not a verdict. As [IEP-0020](https://ieps.iscc.codes/iep-0020/) explains,
similar ISCC-UNITs make a manifest a *candidate*; the client confirms it, for example by comparing the claim
thumbnail of the recovered manifest with the asset, and validates the manifest as usual.

## Fetching the manifest

C2PA clients fetch a manifest from `{endpoint}/manifests/{manifestId}`. Because the endpoint names the
declaration, the resolver needs no memory to answer: it looks the declaration up on the aggregator, checks that
its gateway URL is the address of the requested manifest ID, fetches the manifest from there, and passes it
through unchanged as `application/c2pa`. Manifests are capped at 10 MB and
10 seconds, and must start like a C2PA Manifest Store. `returnActiveManifest` is accepted and ignored.

Clients that ignore `endpoint` fetch from `GET /v1/manifests/{manifestId}` instead. Nothing in IDP is indexed by
manifest ID, so this route only knows the manifest IDs that a query to the same resolver process returned within
the last hour. The first declaration returned for a manifest ID keeps it for that hour, also when another
repository uses the same ID. Any other ID gets `404`.

## Trust

The resolver returns candidates, never endorsements. Anyone can declare on IDP with any manifest address, so the
party that declared and the party that signed a manifest are different identities. Clients decide trust by
validating the recovered manifest, as for any C2PA Manifest. Every match carries the extension field `isccId`,
the ISCC-ID of the declaration behind it, so clients can inspect who declared and when.
