// File check on the landing page. Reads and validates the Content Credentials of a dropped file in the browser with
// c2pa-web, loaded on first use. On request, it searches this resolver for C2PA Manifests of the same content: with
// the io.iscc.v0 soft binding in the file's manifest, or else with the ISCC-UNITs that web.iscc.io computes from an
// upload of the file, which is deleted right after. Manifest data is untrusted and only ever set as text.

import {
  assetFormat,
  isDetached,
  manifestUrl,
  similarityLabel,
  summarize,
  unitsToValue,
  uploadName,
} from "./credentials.js";

const C2PA_WEB = "/static/vendor/c2pa-web-0.15.3/";
const TRUST_LIST = "/static/vendor/c2pa-trust-list/C2PA-TRUST-LIST.pem";
const ISCC_WEB = "https://web.iscc.io";
const NETWORK = document.body.dataset.network;
const POINTER = window.matchMedia("(hover: hover)").matches;
const IDLE = POINTER ? ["Drop a file here", "or click to choose one"] : ["Choose a file", "Image, video, audio or document"];
const AGAIN = POINTER ? "Drop or click to check another file" : "Tap to check another file";
const VERDICTS = {
  trusted: ["Trusted", "Valid, and signed with a certificate on the C2PA trust list."],
  valid: ["Valid", "Valid, but the signer is not on the C2PA trust list."],
  invalid: ["Invalid", "These Content Credentials failed validation:"],
};
const ISCC_WEB_ERRORS = {
  413: "The file is too large for web.iscc.io.",
  422: "web.iscc.io cannot compute an ISCC for this file.",
  429: "web.iscc.io accepts 10 files per minute and 500 per day. Please try again later.",
};
const ui = {
  drop: document.getElementById("drop"),
  dropTitle: document.getElementById("drop-title"),
  dropHint: document.getElementById("drop-hint"),
  input: document.getElementById("file"),
  results: document.getElementById("results"),
  another: document.getElementById("another"),
  checked: document.getElementById("checked"),
  offer: document.getElementById("offer"),
  offerTitle: document.getElementById("offer-title"),
  offerText: document.getElementById("offer-text"),
  search: document.getElementById("search"),
  message: document.getElementById("message"),
  found: document.getElementById("found"),
};
let sdk = null; // promise of the c2pa-web instance
let current = null; // the file being checked: { run, file, preview, summary }
let runs = 0;
let objectUrls = [];

/** Create an element with properties and children; strings become text nodes, nulls are skipped. */
function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((child) => child !== null));
  return node;
}

/** Show the state of the drop area: idle, busy or done, with a title and a hint. */
function setDrop(state, title, hint) {
  ui.drop.dataset.state = state;
  ui.dropTitle.textContent = title;
  ui.dropHint.textContent = hint;
}

/** Show a status line in the results; `kind` is busy, error or empty. */
function say(text, kind = "") {
  ui.message.dataset.kind = kind;
  ui.message.textContent = text;
}

/** Scroll an element into view if its top is below the visible part of the page. */
function reveal(element) {
  if (element.getBoundingClientRect().top > window.innerHeight - 120) element.scrollIntoView({ block: "start" });
}

/** An object URL that is revoked when the next file is checked. */
function objectUrl(blob) {
  const url = URL.createObjectURL(blob);
  objectUrls.push(url);
  return url;
}

/** Fetch a same-origin text resource. */
async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.text();
}

/** Create the c2pa-web instance with the C2PA trust list. */
async function initSdk() {
  const [{ createC2pa }, trustAnchors] = await Promise.all([import(C2PA_WEB + "index.js"), fetchText(TRUST_LIST)]);
  return createC2pa({ wasmSrc: C2PA_WEB + "c2pa_bg.wasm", settings: { trust: { trustAnchors } } });
}

/** The c2pa-web instance, created once; a failed attempt is retried on the next call. */
function loadSdk() {
  sdk ??= initSdk().catch((error) => {
    sdk = null;
    throw error;
  });
  return sdk;
}

/** An object URL of the thumbnail of a manifest, or null. */
async function thumbnailUrl(reader, thumbnail) {
  if (!thumbnail) return null;
  try {
    const bytes = await reader.resourceToBytes(thumbnail.identifier);
    return objectUrl(new Blob([bytes], { type: thumbnail.format }));
  } catch {
    return null;
  }
}

/** Read and summarize the Content Credentials of a blob; null if it has none. */
async function readCredentials(format, blob) {
  const c2pa = await loadSdk();
  // c2pa.reader applies the trust settings of createC2pa; the static Reader.fromBlob would silently ignore them
  const reader = await c2pa.reader.fromBlob(format, blob);
  if (!reader) return null;
  try {
    const summary = summarize(await reader.manifestStore(), isDetached(format));
    return summary && { ...summary, image: await thumbnailUrl(reader, summary.thumbnail) };
  } finally {
    await reader.free();
  }
}

/** A friendly text for an error of the C2PA reader. */
function readerError(error) {
  const text = String(error?.message ?? error);
  return text.includes("UnsupportedType") ? "The C2PA reader does not support this file format." : text;
}

/** A file size in KB or MB. */
function formatSize(bytes) {
  return bytes < 1e6 ? `${Math.max(1, Math.round(bytes / 1e3))} KB` : `${(bytes / 1e6).toFixed(1)} MB`;
}

/** A timestamp in the viewer's locale. */
function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** The upper-case extension of a file name, as a placeholder label. */
function extension(name) {
  return name.includes(".") ? name.split(".").pop().toUpperCase() : "File";
}

/** A captioned image, or a placeholder with a label when there is no image. */
function figure(image, caption, placeholder) {
  const content = image ? el("img", { src: image, alt: caption }) : placeholder;
  return el("figure", { className: "figure" }, [
    el("div", { className: "figure__frame" }, [content]),
    el("figcaption", {}, [caption]),
  ]);
}

/** A verdict badge; `level` is trusted, valid, invalid or none. */
function badge(level, text) {
  return el("span", { className: `badge badge--${level}` }, [text]);
}

/** A definition list of the rows that have a value. */
function facts(rows) {
  const items = rows
    .filter(([, value]) => value)
    .flatMap(([term, value]) => [el("dt", {}, [term]), el("dd", {}, [value])]);
  return el("dl", { className: "facts" }, items);
}

/** The explanation of a verdict, with its failure codes and a note on unchecked hashes. */
function explain(verdict) {
  const codes = verdict.level === "invalid" ? el("p", { className: "codes" }, [verdict.failures.join(", ")]) : null;
  const note = verdict.hashUnchecked
    ? el("p", { className: "note" }, ["The file hash is not checked: this Manifest Store was read without its file."])
    : null;
  return [el("p", { className: "explain" }, [VERDICTS[verdict.level][1]]), codes, note];
}

/** The rows that describe a manifest summary. */
function summaryRows(summary) {
  return [
    ["Signer", summary.signer],
    ["Issued by", summary.issuer],
    ["Signed", summary.signedAt && formatTime(summary.signedAt)],
    ["Made with", summary.generator],
  ];
}

/** A placeholder card while a file is read. */
function loadingCard(text) {
  return el("div", { className: "card card--loading" }, [el("span", { className: "spinner" }), text]);
}

/** A card for a file without readable Content Credentials. */
function bareCard(file, preview, problem, detached) {
  const text = problem
    ? problem
    : detached
      ? "This Manifest Store holds no manifest."
      : "This file carries no C2PA Manifest. If it had Content Credentials, a platform may have stripped them: " +
        "search for them by content below.";
  return el("article", { className: "card" }, [
    figure(preview, "Your file", extension(file.name)),
    el("div", { className: "card__body" }, [
      el("div", { className: "tags" }, [badge("none", problem ? "Not readable" : "No Content Credentials")]),
      el("h3", {}, [file.name]),
      el("p", { className: "explain" }, [text]),
      facts([["Size", formatSize(file.size)]]),
    ]),
  ]);
}

/** A card for a checked file with Content Credentials: thumbnail, verdict and manifest facts. */
function credentialsCard(file, preview, summary) {
  const image = summary.image ?? preview;
  return el("article", { className: "card" }, [
    figure(image, summary.image ? "Claim thumbnail" : "Your file", extension(file.name)),
    el("div", { className: "card__body" }, [
      el("div", { className: "tags" }, [badge(summary.verdict.level, VERDICTS[summary.verdict.level][0])]),
      el("h3", {}, [summary.title ?? file.name]),
      ...explain(summary.verdict),
      facts([
        ["File", `${file.name} · ${formatSize(file.size)}`],
        ...summaryRows(summary),
        ["ISCC soft binding", summary.isccValue ? "Yes" : "None"],
      ]),
    ]),
  ]);
}

/** Explain and show the search that fits the checked file; a Manifest Store without ISCC has none. */
function showOffer(summary, detached) {
  if (!summary?.isccValue && detached) return;
  if (summary?.isccValue) {
    ui.offerTitle.textContent = "Search with its ISCC";
    ui.offerText.replaceChildren(
      `Its Content Credentials carry an ISCC soft binding. Search the ISCC ${NETWORK} for C2PA Manifests ` +
        "declared for the same content. Only the ISCC is sent; the file stays in your browser.",
    );
    ui.search.textContent = "Search";
  } else {
    ui.offerTitle.textContent = "Search for lost Content Credentials";
    ui.offerText.replaceChildren(
      `To search the ISCC ${NETWORK} by content, the file is uploaded to `,
      el("a", { href: ISCC_WEB }, ["web.iscc.io"]),
      ", the ISCC generator of the ISCC Foundation, which computes its ISCC. The upload is deleted right after, " +
        "at the latest after one hour.",
    );
    ui.search.textContent = "Upload and search";
  }
  ui.offer.hidden = false;
}

/** Clear the results and release the object URLs of the previous file. */
function reset() {
  objectUrls.forEach((url) => URL.revokeObjectURL(url));
  objectUrls = [];
  ui.found.replaceChildren();
  ui.offer.hidden = true;
  ui.search.disabled = false;
  say("");
}

/** Step 1: read and validate the Content Credentials of a file, without sending it anywhere. */
async function check(file) {
  if (!file) return;
  reset();
  const run = ++runs;
  const preview = file.type.startsWith("image/") ? objectUrl(file) : null;
  current = { run, file, preview, summary: null };
  const status = sdk ? `Reading ${file.name}...` : "Loading the C2PA reader (2 MB, once)...";
  setDrop("busy", file.name, formatSize(file.size));
  ui.checked.replaceChildren(loadingCard(status));
  ui.results.hidden = false;
  reveal(ui.results);
  const format = assetFormat(file.name, file.type);
  let summary = null;
  let problem = null;
  try {
    summary = await readCredentials(format, file);
  } catch (error) {
    problem = readerError(error);
  }
  if (run !== runs) return;
  current.summary = summary;
  setDrop("done", file.name, AGAIN);
  const card = summary
    ? credentialsCard(file, preview, summary)
    : bareCard(file, preview, problem, isDetached(format));
  ui.checked.replaceChildren(card);
  showOffer(summary, isDetached(format));
}

/** Upload a file to web.iscc.io, return the io.iscc.v0 value of its units, and delete the upload. */
async function computeValue(file) {
  let response;
  try {
    response = await fetch(`${ISCC_WEB}/api/v1/iscc?granular=false`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Upload-Filename": uploadName(file.name) },
      body: file,
    });
  } catch {
    throw new Error("web.iscc.io is not reachable. Please try again later.");
  }
  if (!response.ok) throw new Error(ISCC_WEB_ERRORS[response.status] ?? `web.iscc.io answered HTTP ${response.status}.`);
  const result = await response.json();
  fetch(`${ISCC_WEB}/api/v1/media/${encodeURIComponent(result.media_id)}`, { method: "DELETE" }).catch(() => {});
  return unitsToValue(result.units);
}

/** Query this resolver with an io.iscc.v0 value. */
async function query(value) {
  const response = await fetch("/v1/matches/byBinding", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alg: "io.iscc.v0", value }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`The search failed: ${JSON.stringify(body.detail ?? response.status)}`);
  return body.matches;
}

/** Fetch and read the Manifest Store of a match; the summary is null if it cannot be fetched or read. */
async function describeMatch(match) {
  try {
    const response = await fetch(manifestUrl(match));
    if (!response.ok) return { match, summary: null, problem: `Manifest unavailable (HTTP ${response.status}).` };
    return { match, summary: await readCredentials("application/c2pa", await response.blob()) };
  } catch (error) {
    return { match, summary: null, problem: `Manifest unreadable: ${readerError(error)}` };
  }
}

/** A card for a match: the visitor's file next to the recovered thumbnail, the verdict and the manifest facts. */
function matchCard({ match, summary, problem }, preview) {
  const candidate = figure(summary?.image ?? null, "Recovered thumbnail", "No thumbnail");
  const level = summary ? summary.verdict.level : "none";
  const label = summary ? VERDICTS[level][0] : "Unavailable";
  const body = summary
    ? [el("h3", {}, [summary.title ?? "Untitled"]), ...explain(summary.verdict)]
    : [el("h3", {}, ["Manifest not available"]), el("p", { className: "explain" }, [problem ?? "No manifest found."])];
  const rows = [...(summary ? summaryRows(summary) : []), ["Declaration", match.isccId]];
  const link = summary ? el("a", { href: manifestUrl(match), className: "card__link" }, ["Download the Manifest Store"]) : null;
  return el("article", { className: "card card--match" }, [
    el("div", { className: "compare" }, [preview ? figure(preview, "Your file", "") : null, candidate]),
    el("div", { className: "card__body" }, [
      el("div", { className: "tags" }, [el("span", { className: "pill" }, [similarityLabel(match.similarityScore)]), badge(level, label)]),
      ...body,
      facts(rows),
      link,
    ]),
  ]);
}

/** Render the matches of a search, best first. */
function showMatches(described, preview) {
  if (!described.length) {
    ui.found.replaceChildren(
      el("div", { className: "empty" }, [
        el("p", {}, [`No C2PA Manifests found for this content on the ISCC ${NETWORK}.`]),
        el("p", { className: "note" }, [
          "Manifests become findable when their repository declares the content with an ISCC.",
        ]),
      ]),
    );
    return;
  }
  const count = described.length === 1 ? "1 candidate" : `${described.length} candidates`;
  ui.found.replaceChildren(
    el("div", { className: "found__head" }, [
      el("h3", {}, [count]),
      el("p", { className: "note" }, [
        "Candidates are found by content similarity. Compare each thumbnail with your file before you rely on a match.",
      ]),
    ]),
    el("div", { className: "matches" }, described.map((entry) => matchCard(entry, preview))),
  );
}

/** Step 2, after the visitor confirms: search for C2PA Manifests of the checked file. */
async function search() {
  const { run, file, preview, summary } = current;
  const label = ui.search.textContent;
  ui.search.disabled = true;
  ui.search.textContent = "Searching...";
  ui.found.replaceChildren();
  try {
    let value = summary?.isccValue;
    if (!value) {
      say("Uploading to web.iscc.io and computing the ISCC...", "busy");
      value = await computeValue(file);
    }
    if (run !== runs) return;
    say(`Searching the ISCC ${NETWORK}...`, "busy");
    const matches = await query(value);
    if (run !== runs) return;
    if (matches.length) say("Reading the manifests of the candidates...", "busy");
    const described = await Promise.all(matches.map(describeMatch));
    if (run !== runs) return;
    say("");
    showMatches(described, preview);
    reveal(ui.found);
  } catch (error) {
    if (run === runs) say(String(error?.message ?? error), "error");
  } finally {
    if (run === runs) {
      ui.search.disabled = false;
      ui.search.textContent = label;
    }
  }
}

/** Highlight the drop area while a file is dragged over it. */
function dragOver(event) {
  event.preventDefault();
  ui.drop.classList.add("drop--active");
}

/** Remove the drop highlight. */
function dragLeave() {
  ui.drop.classList.remove("drop--active");
}

/** Check a dropped file. */
function dropped(event) {
  event.preventDefault();
  dragLeave();
  check(event.dataTransfer.files[0]);
}

/** Check a chosen file; clearing the input lets the same file be chosen again. */
function chosen() {
  const file = ui.input.files[0];
  ui.input.value = "";
  check(file);
}

/** Open the file chooser of the drop area. */
function chooseAnother() {
  ui.input.click();
}

/** Keep the browser from opening files dropped next to the drop area. */
function ignoreDrop(event) {
  event.preventDefault();
}

setDrop("idle", ...IDLE);
ui.drop.addEventListener("dragover", dragOver);
ui.drop.addEventListener("dragleave", dragLeave);
ui.drop.addEventListener("drop", dropped);
ui.input.addEventListener("change", chosen);
ui.another.addEventListener("click", chooseAnother);
ui.search.addEventListener("click", search);
window.addEventListener("dragover", ignoreDrop);
window.addEventListener("drop", ignoreDrop);
