---
title: Gateway contract
description: How a C2PA Manifest repository makes its manifests findable through ISCC C2PA resolvers
icon: lucide/link
---

# Gateway contract

!!! warning "Draft"

    This contract is a draft and may still change.

A C2PA Manifest repository with a Soft Binding Resolution API becomes findable through every ISCC C2PA resolver by
declaring its assets with their manifest addresses as gateway URLs. It hosts nothing new.
{ .iscc-lead }

## Declare the manifest address

Declare the ISCC-UNITs of the content on an ISCC hub, following the
[ISCC Discovery Protocol](https://ieps.iscc.codes/iep-0013/). Declare the units of the asset's `io.iscc.v0` soft
binding, so that the declaration says what the manifest says. As the declaration's gateway URL, use the address at
which your Soft Binding Resolution API serves the manifest:

```text
https://repo.example/v1/manifests/urn:c2pa:F9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4
```

| Part                      | Rule                                                                    |
| ------------------------- | ----------------------------------------------------------------------- |
| `https://repo.example/v1` | The base of your API: any HTTPS address                                 |
| `/manifests/`             | The manifest route of the C2PA Soft Binding Resolution API              |
| `urn:c2pa:…`              | The ID under which your API serves the manifest, percent-encoded or not |

Resolvers read the manifest ID from the URL: the path after the last `/manifests/`, percent-decoded. C2PA
recommends the URN of the active manifest (`urn:c2pa:<UUID>`, possibly with a claim generator and version part);
an identifier of your own works too. It has at most 256 characters and no whitespace or control characters. The
URL has no query and no fragment, as IEP-0013 requires.

Declarations cannot be changed. The address stays in the signed declaration, so keep it working, or redirect it:
resolvers follow up to two redirects.

## Serve the manifest

The address must:

- serve the C2PA Manifest Store as `application/c2pa`;
- allow anonymous reads;
- answer within 3 seconds with the first bytes, and within 10 seconds with at most 10 MB.

The `GET /manifests/{manifestId}` route of a Soft Binding Resolution API qualifies. So does a static file at a path
of the same shape, for example `https://example.org/c2pa/manifests/urn:c2pa:F9168C5E-CEB2-4FAA-B6BF-329BF39FA1E4`:
resolvers check the first bytes of the response, not its media type. Resolvers pass the manifest through to their
clients, so the address needs no CORS headers.

## What resolvers do with it

- A gateway URL without `/manifests/` in its path is never fetched. Ordinary gateway documents do not take part.
- The first time a resolver sees a manifest address, it reads the first 32 bytes and checks that they start a C2PA
    Manifest Store. It remembers the answer for a day, for an hour if the address served something else or
    nothing, and for a minute if it failed or timed out.
- When a client fetches the manifest, the resolver reads it in full and passes it through unchanged.

The manifest exists before you declare, so publication needs no particular order.
