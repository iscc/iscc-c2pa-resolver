// Tests of the pure helpers of the landing page file check (static/credentials.js), run with `node --test`.
// The Manifest Store fixtures are what c2pa-web 0.15.3 reports for a JPEG signed by the ISCC C2PA demo app (with
// and without its test root as trust anchor) and for the c2pa-rs test Manifest Store read on its own.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  activeManifest,
  assetFormat,
  base32Decode,
  isccBinding,
  isDetached,
  manifestUrl,
  similarityLabel,
  summarize,
  toBase64,
  unitsToValue,
  uploadName,
  verdict,
} from "../../iscc_c2pa_resolver/static/credentials.js";

const DATA = new URL("../data/", import.meta.url);
const UNTRUSTED = load("store-iscc-untrusted.json");
const TRUSTED = load("store-iscc-trusted.json");
const DETACHED = load("store-detached.json");
// Units of a generated test image as computed by web.iscc.io, and their value per iscc-core `encode_seq`
const UNITS = [
  "ISCC:AADYQFSE3ICYYADA7YEUUG6VCATWB6HKTQPYHE5H5PBYJRDJPXKBOAY",
  "ISCC:EEDYAAIVB4OX6H37AABAUFJ374756AIFBIOW6H3PL4AAUFI334HF7PY",
  "ISCC:GAD2IKZZNOKUMNGHAAQILVGVZCU3CIDEWQCVIELHTIDQNZWLC7HA5DY",
  "ISCC:IAD5BQAMVA73U5YFPFUMHVTTKLNLDD2UVAQX7ENI5ICH5N6N2EYQH3I",
];
const UNITS_VALUE =
  "AAeIFkTaBYwAYP4JShvVECdg+OqcH4OTp+vDhMRpfdQXAyEHgAEVDx1/H38AAgoVO/8/3wEFCh1vH29fAAoVG98OX78wB6QrOWuVRjTHACCF1NXIqbEgZLQFVBFnmgcG5ssXzg6PQAfQwAyoP7p3BXlow9ZzUtqxj1SoIX+RqOoEfrfN0TED7Q==";
// The io.iscc.v0 value in the demo manifest, as base64 of its CBOR byte string
const BINDING_VALUE =
  "AAe2zpd/kTqwOc2+bd3bv21DW8EQHa+n4FEft0d7plCYeyEHqtJHo1riD1xVpY5GtcQeudJHo1rzD1wopY5GtcYeuVEwBzHxNDpcO+2+rGxyOTQUn50LlUIbzjVTyWZeTyF3PpAUQAdGeP3q68i60OTlxQTMdqrvXiOlcQu/mQPQRjd7A0ENDg==";

/** Read a JSON fixture from tests/data. */
function load(name) {
  return JSON.parse(readFileSync(new URL(name, DATA), "utf-8"));
}

/** A Manifest Store whose active manifest has the given assertions. */
function storeWith(assertions) {
  return { active_manifest: "m", manifests: { m: { assertions } } };
}

/** A soft binding assertion with one block holding `value`. */
function softBinding(alg, value) {
  return { label: "c2pa.soft-binding", data: { alg, blocks: [{ scope: {}, value }] } };
}

test("base32Decode decodes unpadded RFC 4648 base32", () => {
  assert.deepEqual(base32Decode("MZXW6YQ"), new TextEncoder().encode("foob"));
  assert.deepEqual(base32Decode(""), new Uint8Array());
});

test("base32Decode rejects characters outside the alphabet", () => {
  assert.throws(() => base32Decode("MZXW6Y1"), /Invalid base32 character: 1/);
});

test("toBase64 encodes bytes with padding", () => {
  assert.equal(toBase64(new TextEncoder().encode("foob")), "Zm9vYg==");
});

test("unitsToValue matches iscc-core encode_seq", () => {
  assert.equal(unitsToValue(UNITS), UNITS_VALUE);
});

test("unitsToValue accepts units without the ISCC: prefix", () => {
  assert.equal(unitsToValue(UNITS.map((unit) => unit.slice(5))), UNITS_VALUE);
});

test("uploadName base64-encodes the UTF-8 file name", () => {
  assert.equal(uploadName("your-media-file.jpg"), "eW91ci1tZWRpYS1maWxlLmpwZw==");
  assert.equal(uploadName("Ä.png"), "w4QucG5n");
});

test("assetFormat reads Manifest Stores by extension, others by MIME type, then extension", () => {
  assert.equal(assetFormat("photo.jpg", "image/jpeg"), "image/jpeg");
  assert.equal(assetFormat("photo.C2PA", ""), "application/c2pa");
  assert.equal(assetFormat("photo.c2pa", "application/x-c2pa-manifest-store"), "application/c2pa");
  assert.equal(assetFormat("clip.MKV", ""), "mkv");
  assert.equal(assetFormat("clip.mkv", "application/octet-stream"), "mkv");
  assert.equal(assetFormat("README", ""), "");
});

test("isDetached is true for Manifest Stores only", () => {
  assert.equal(isDetached("application/c2pa"), true);
  assert.equal(isDetached("image/jpeg"), false);
});

test("activeManifest returns null for missing stores and labels", () => {
  assert.equal(activeManifest(null), null);
  assert.equal(activeManifest({ manifests: {}, active_manifest: "x" }), null);
  assert.equal(activeManifest(UNTRUSTED).title, "no manifest");
});

test("isccBinding reads the byte string value of a real manifest", () => {
  assert.equal(isccBinding(activeManifest(UNTRUSTED)), BINDING_VALUE);
});

test("isccBinding takes a text value as it is", () => {
  assert.equal(isccBinding(activeManifest(storeWith([softBinding("io.iscc.v0", "AAEC")]))), "AAEC");
});

test("isccBinding skips other algorithms, other assertions and empty blocks", () => {
  const assertions = [
    { label: "c2pa.actions.v2", data: {} },
    softBinding("com.example.watermark", "AAEC"),
    softBinding("io.iscc.v0", ""),
    { label: "c2pa.soft-binding__1", data: { alg: "io.iscc.v0", blocks: [{ value: [1, 2, 3] }] } },
  ];
  assert.equal(isccBinding(activeManifest(storeWith(assertions))), "AQID");
});

test("isccBinding returns null without an ISCC soft binding", () => {
  assert.equal(isccBinding(activeManifest(DETACHED)), null);
  assert.equal(isccBinding(activeManifest(storeWith([softBinding("io.iscc.v0", 7)]))), null);
  assert.equal(isccBinding({ assertions: [{ label: "c2pa.soft-binding", data: { alg: "io.iscc.v0" } }] }), null);
  assert.equal(isccBinding(null), null);
});

test("verdict reports an untrusted signer as valid", () => {
  assert.deepEqual(verdict(UNTRUSTED, false), {
    level: "valid",
    failures: ["signingCredential.untrusted"],
    hashUnchecked: false,
  });
});

test("verdict reports a signer on the trust list as trusted", () => {
  assert.deepEqual(verdict(TRUSTED, false), { level: "trusted", failures: [], hashUnchecked: false });
});

test("verdict leaves out hard binding mismatches of a detached Manifest Store", () => {
  assert.deepEqual(verdict(DETACHED, true), {
    level: "valid",
    failures: ["signingCredential.untrusted"],
    hashUnchecked: true,
  });
});

test("verdict counts hard binding mismatches of an embedded manifest", () => {
  assert.deepEqual(verdict(DETACHED, false), {
    level: "invalid",
    failures: ["assertion.dataHash.mismatch"],
    hashUnchecked: false,
  });
});

test("verdict falls back to validation_status without validation_results", () => {
  const store = { validation_status: [{ code: "claimSignature.mismatch" }] };
  assert.deepEqual(verdict(store, false), {
    level: "invalid",
    failures: ["claimSignature.mismatch"],
    hashUnchecked: false,
  });
  assert.equal(verdict({}, false).level, "valid");
});

test("summarize lists the display fields of a real manifest", () => {
  assert.deepEqual(summarize(UNTRUSTED, false), {
    title: "no manifest",
    signer: "C2PA Signer",
    issuer: "C2PA Test Signing Cert",
    signedAt: "2026-09-27T13:08:58+00:00",
    generator: "ISCC C2PA Demo 0.1.0",
    thumbnail: activeManifest(UNTRUSTED).thumbnail,
    isccValue: BINDING_VALUE,
    verdict: verdict(UNTRUSTED, false),
  });
});

test("summarize of a detached Manifest Store", () => {
  const summary = summarize(DETACHED, true);
  assert.equal(summary.title, "C.jpg");
  assert.equal(summary.generator, "make_test_images 0.33.1");
  assert.equal(summary.thumbnail.format, "image/jpeg");
  assert.equal(summary.verdict.hashUnchecked, true);
});

test("summarize tolerates sparse manifests", () => {
  const sparse = storeWith([]);
  assert.deepEqual(summarize(sparse, false), {
    title: null,
    signer: null,
    issuer: null,
    signedAt: null,
    generator: null,
    thumbnail: null,
    isccValue: null,
    verdict: { level: "valid", failures: [], hashUnchecked: false },
  });
  sparse.manifests.m.claim_generator = "legacy/1.0";
  sparse.manifests.m.thumbnail = { format: "application/octet-stream", identifier: "x" };
  assert.equal(summarize(sparse, false).generator, "legacy/1.0");
  assert.equal(summarize(sparse, false).thumbnail, null);
  sparse.manifests.m.claim_generator_info = [{ name: "tool" }];
  assert.equal(summarize(sparse, false).generator, "tool");
});

test("summarize returns null without an active manifest", () => {
  assert.equal(summarize({}, false), null);
});

test("similarityLabel names identical files and percentages", () => {
  assert.equal(similarityLabel(100), "Identical file");
  assert.equal(similarityLabel(93), "93 % similar");
});

test("manifestUrl appends the encoded manifest ID to the endpoint", () => {
  const match = { endpoint: "https://c2pa.iscc.io/v1/iscc/maigkv5faaxoxyab", manifestId: "urn:c2pa:a/b" };
  assert.equal(manifestUrl(match), "https://c2pa.iscc.io/v1/iscc/maigkv5faaxoxyab/manifests/urn%3Ac2pa%3Aa%2Fb");
});
