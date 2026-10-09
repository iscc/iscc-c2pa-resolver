---
title: Development
description: Set up, test and release the ISCC C2PA Resolver
icon: lucide/code
---

# Development

Python 3.12+, [uv](https://docs.astral.sh/uv/) for environments and [poe](https://poethepoet.natn.io/) for tasks.
{ .iscc-lead }

## Setup

```bash
uv sync --all-groups
uv run prek install   # git hooks: ruff, mdformat, lockfile, workflow and type checks
```

## Tasks

| Command                  | What it does                                                    |
| ------------------------ | --------------------------------------------------------------- |
| `uv run poe all`         | Format, type check and test (the local loop)                    |
| `uv run poe ci`          | Lint, type check, test and audit dependencies, changing nothing |
| `uv run poe test`        | Tests with the 100% branch coverage gate                        |
| `uv run poe test-js`     | JavaScript tests of the landing page (Node 22.8+)               |
| `uv run poe serve`       | Dev server on `http://127.0.0.1:45460`                          |
| `uv run poe conformance` | C2PA conformance harness against the dev server                 |
| `uv run poe docs-serve`  | Documentation preview                                           |

## API contract

The contract is spec-first. `iscc_c2pa_resolver/openapi/openapi.yaml` is written by hand; its `c2pa.*` schemas are
references into `c2pa-sbr.json`, a verbatim subset of the C2PA Soft Binding Resolution API definition.

| Step                    | Command                | What it does                                               |
| ----------------------- | ---------------------- | ---------------------------------------------------------- |
| Follow upstream changes | `uv run poe sync-c2pa` | Copies the implemented subset from a specs-core checkout   |
| Regenerate models       | `uv run poe codegen`   | Generates `iscc_c2pa_resolver/schema/` from `openapi.yaml` |

After a sync, the git diff of `c2pa-sbr.json` shows what changed upstream. `tests/test_openapi.py` checks that every
upstream operation keeps its operationId, parameters and response schemas in `openapi.yaml`, and
`tests/test_schemathesis.py` fuzzes every route against the spec with Schemathesis. Property-based tests with
Hypothesis cover the parsers of untrusted input (`tests/test_properties.py`).

## Landing page file check

The file check on the landing page runs [c2pa-web](https://github.com/contentauth/c2pa-js), c2pa-rs compiled to
WebAssembly, in the visitor's browser. `uv run poe vendor-c2pa-web` downloads the pinned npm packages, verifies them
against the registry's integrity hashes and writes them unmodified to `iscc_c2pa_resolver/static/vendor/`, together
with the C2PA trust list. The 9 MB WebAssembly module is stored only precompressed (`.br` 2.2 MB, `.gz` 3.2 MB);
the app serves the coding the browser accepts. After a version change, update the paths in `static/index.html` and
`static/check.js`. The tests check that the served module matches the integrity hash that c2pa-web requires.

`static/credentials.js` holds the pure logic (manifest summaries, validation verdicts, `io.iscc.v0` values) and is
tested with `node --test` against Manifest Stores that c2pa-web reported for real signed files. `static/check.js`
holds the page logic.

## Quality gates

Every pull request and every push to `main` runs:

- ruff (format and lint, including security and complexity rules), mdformat, pyright, openapi-spec-validator, and
    a check that the generated models match `openapi.yaml`;
- pytest with 100% branch coverage on Python 3.12, 3.13 and 3.14, including the Schemathesis and Hypothesis suites;
- `node --test` with 100% coverage of the pure JavaScript of the landing page;
- `uv audit` against known vulnerabilities in locked dependencies;
- the prek hooks, including zizmor for GitHub Actions security;
- a container build that must pass its health check and the C2PA Soft Binding API conformance harness.

## Release

1. Bump `version` in `pyproject.toml` on `main` and wait for CI to publish the `sha-<sha>` image.
1. Create a GitHub Release `vX.Y.Z` on that commit. The release workflow checks that the tag matches the version
    and retags the tested image as `X.Y.Z` and `latest`.
