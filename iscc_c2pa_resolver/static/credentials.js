// Pure helpers of the file check on the landing page: summaries of C2PA Manifest Stores as c2pa-web reports them,
// and `io.iscc.v0` soft binding values (base64 ISCC-SEQ, IEP-0020). No DOM or network access, so Node runs the tests.

export const ALG = "io.iscc.v0";
const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const TRUSTED = "signingCredential.trusted";
const UNTRUSTED = "signingCredential.untrusted";
// Hard binding failures that a Manifest Store checked apart from its asset always reports: there is nothing to hash.
const HARD_BINDING_MISMATCHES = new Set([
  "assertion.dataHash.mismatch",
  "assertion.bmffHash.mismatch",
  "assertion.boxesHash.mismatch",
  "assertion.collectionHash.mismatch",
]);

/** Decode unpadded RFC 4648 base32 text into bytes. */
export function base32Decode(text) {
  const bytes = [];
  let buffer = 0;
  let bits = 0;
  for (const char of text) {
    const value = BASE32.indexOf(char);
    if (value < 0) throw new Error(`Invalid base32 character: ${char}`);
    buffer = (buffer << 5) | value;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 0xff);
    }
  }
  return Uint8Array.from(bytes);
}

/** Encode bytes as standard base64 with padding. */
export function toBase64(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** The `io.iscc.v0` value of ISCC-UNITs such as `ISCC:EEDY...`: the base64 of their concatenated bytes. */
export function unitsToValue(units) {
  const parts = units.map((unit) => base32Decode(unit.replace(/^ISCC:/, "")));
  const seq = new Uint8Array(parts.reduce((size, part) => size + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    seq.set(part, offset);
    offset += part.length;
  }
  return toBase64(seq);
}

/** The base64 file name that web.iscc.io expects in `X-Upload-Filename`. */
export function uploadName(name) {
  return toBase64(new TextEncoder().encode(name));
}

/** The format to read a file as: `application/c2pa` for a Manifest Store, its MIME type, or else its extension.
 *
 * Browsers report the type that the operating system maps to an extension, which for `.c2pa` is often generic.
 */
export function assetFormat(name, type) {
  const extension = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  if (extension === "c2pa") return "application/c2pa";
  return type && type !== "application/octet-stream" ? type : extension;
}

/** Whether a format is a Manifest Store on its own, without the asset it describes. */
export function isDetached(format) {
  return format === "application/c2pa";
}

/** The active manifest of a Manifest Store, or null. */
export function activeManifest(store) {
  return store?.manifests?.[store.active_manifest] ?? null;
}

/** The base64 `io.iscc.v0` value of the first ISCC soft binding of a manifest, or null.
 *
 * c2pa-web renders CBOR byte strings as arrays of numbers; a text value is taken as it is.
 */
export function isccBinding(manifest) {
  for (const assertion of manifest?.assertions ?? []) {
    if (!assertion.label?.startsWith("c2pa.soft-binding") || assertion.data?.alg !== ALG) continue;
    for (const block of assertion.data.blocks ?? []) {
      if (Array.isArray(block.value)) return toBase64(Uint8Array.from(block.value));
      if (typeof block.value === "string" && block.value) return block.value;
    }
  }
  return null;
}

/** The status codes of a validation result list. */
function codes(entries) {
  return (entries ?? []).map((entry) => entry.code);
}

/** The validation verdict of the active manifest.
 *
 * `level` is `trusted` (valid and signed by a certificate on the trust list), `valid` (valid, signer not trusted) or
 * `invalid`; `failures` lists the failure codes that decide it. With `detached`, hard binding mismatches are left
 * out and reported as `hashUnchecked`, because a Manifest Store fetched on its own has no asset to compare.
 */
export function verdict(store, detached) {
  const results = store?.validation_results?.activeManifest;
  const all = codes(results ? results.failure : store?.validation_status);
  const hashUnchecked = detached && all.some((code) => HARD_BINDING_MISMATCHES.has(code));
  const failures = all.filter((code) => !(detached && HARD_BINDING_MISMATCHES.has(code)));
  const problems = failures.filter((code) => code !== UNTRUSTED);
  if (problems.length) return { level: "invalid", failures: problems, hashUnchecked };
  const trusted = codes(results?.success).includes(TRUSTED) && !failures.includes(UNTRUSTED);
  return { level: trusted ? "trusted" : "valid", failures, hashUnchecked };
}

/** The name and version of the application that made a manifest, or null. */
function generator(manifest) {
  const info = manifest.claim_generator_info?.[0];
  if (!info?.name) return manifest.claim_generator ?? null;
  return info.version ? `${info.name} ${info.version}` : info.name;
}

/** The display fields of the active manifest of a Manifest Store, or null if it has none. */
export function summarize(store, detached) {
  const manifest = activeManifest(store);
  if (!manifest) return null;
  const signature = manifest.signature_info ?? {};
  return {
    title: manifest.title ?? null,
    signer: signature.common_name ?? null,
    issuer: signature.issuer ?? null,
    signedAt: signature.time ?? null,
    generator: generator(manifest),
    thumbnail: manifest.thumbnail?.format?.startsWith("image/") ? manifest.thumbnail : null,
    isccValue: isccBinding(manifest),
    verdict: verdict(store, detached),
  };
}

/** The similarity of a match in words; the resolver scores 100 only for an equal Instance-Code. */
export function similarityLabel(score) {
  return score >= 100 ? "Identical file" : `${score} % similar`;
}

/** The address of a match's C2PA Manifest Store on this resolver. */
export function manifestUrl(match) {
  return `${match.endpoint}/manifests/${encodeURIComponent(match.manifestId)}`;
}
