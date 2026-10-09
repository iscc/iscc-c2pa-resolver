"""Vendor the in-browser C2PA reader that the landing page uses to check dropped files.

Downloads pinned npm packages, verifies them against the integrity hashes of the npm registry, and writes the files
the page loads to `iscc_c2pa_resolver/static/vendor/`:

- `c2pa-web-<version>/` - the ES module build of `@contentauth/c2pa-web` (MIT, c2pa-rs compiled to WebAssembly).
    The WebAssembly module is stored only precompressed, as `c2pa_bg.wasm.br` and `c2pa_bg.wasm.gz`; the app serves
    the encoding the browser accepts.
- `highgain-<version>/` - the one runtime import of c2pa-web (ISC), resolved by the import map of the landing page.
- `c2pa-trust-list/` - the C2PA trust list (CC BY 4.0) from c2pa-org/conformance-public, at its latest commit.

Package files are written unmodified. Rerun after changing a version, then update the paths in `static/index.html`
and `static/check.js`; the git diff of the output shows what changed.

Usage:

    uv run poe vendor-c2pa-web
"""

import base64
import gzip
import hashlib
import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import brotli
import httpx2

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "iscc_c2pa_resolver" / "static" / "vendor"
C2PA_WEB = ("@contentauth/c2pa-web", "0.15.3")
HIGHGAIN = ("highgain", "0.1.0")
WASM = "dist/resources/c2pa_bg.wasm"
SKIPPED_SCRIPTS = {"dist/inline.js", "dist/c2pa_worker.js"}  # the base64-inlined build and the standalone worker
TRUST_REPO = "c2pa-org/conformance-public"
TRUST_FILE = "trust-list/C2PA-TRUST-LIST.pem"


def fetch(client, url, params=None):
    # type: (httpx2.Client, str, dict | None) -> httpx2.Response
    """Download a URL and fail on any HTTP error."""
    response = client.get(url, params=params)
    response.raise_for_status()
    return response


def check_integrity(data, integrity):
    # type: (bytes, str) -> None
    """Compare data with an npm `sha512-<base64>` integrity string.

    :raises ValueError: if the digest differs or the integrity string is not SHA-512
    """
    algorithm, _, expected = integrity.partition("-")
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
        raise ValueError(f"Integrity mismatch: {integrity}")


def npm_files(client, name, version):
    # type: (httpx2.Client, str, str) -> dict[str, bytes]
    """Download an npm package tarball, verify it, and return its files by path relative to the package root."""
    meta = fetch(client, f"https://registry.npmjs.org/{name}/{version}").json()
    tarball = fetch(client, meta["dist"]["tarball"]).content
    check_integrity(tarball, meta["dist"]["integrity"])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile():
                files[str(PurePosixPath(member.name).relative_to("package"))] = archive.extractfile(member).read()  # type: ignore[union-attr]
    return files


def runtime_scripts(files):
    # type: (dict[str, bytes]) -> dict[str, bytes]
    """The top-level `dist/*.js` modules of a package, without the builds the page does not load."""
    return {
        path: data
        for path, data in files.items()
        if PurePosixPath(path).parent == PurePosixPath("dist") and path.endswith(".js") and path not in SKIPPED_SCRIPTS
    }


def write(directory, name, data):
    # type: (Path, str, bytes) -> None
    """Write one vendored file and report it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    print(f"{(directory / name).relative_to(ROOT)}  {len(data):,} bytes")


def replace_directories(pattern):
    # type: (str) -> None
    """Remove earlier vendored versions of a package."""
    for old in VENDOR.glob(pattern):
        shutil.rmtree(old)


def vendor_c2pa_web(client):
    # type: (httpx2.Client) -> None
    """Write the c2pa-web modules, license, package manifest and precompressed WebAssembly module."""
    name, version = C2PA_WEB
    files = npm_files(client, name, version)
    target = VENDOR / f"c2pa-web-{version}"
    replace_directories("c2pa-web-*")
    for path, data in runtime_scripts(files).items():
        write(target, PurePosixPath(path).name, data)
    write(target, "LICENSE", files["LICENSE"])
    write(target, "package.json", files["package.json"])
    wasm = files[WASM]
    write(target, "c2pa_bg.wasm.gz", gzip.compress(wasm, compresslevel=9, mtime=0))
    write(target, "c2pa_bg.wasm.br", brotli.compress(wasm, quality=11))


def vendor_highgain(client):
    # type: (httpx2.Client) -> None
    """Write the highgain module and its package manifest, which carries the ISC license notice."""
    name, version = HIGHGAIN
    files = npm_files(client, name, version)
    target = VENDOR / f"highgain-{version}"
    replace_directories("highgain-*")
    write(target, "index.js", files["dist/index.js"])
    write(target, "package.json", files["package.json"])


def trust_list_readme(commit):
    # type: (str) -> bytes
    """Attribution and provenance of the vendored trust list."""
    return (
        "# C2PA trust list\n\n"
        f"`C2PA-TRUST-LIST.pem` is an unmodified copy of `{TRUST_FILE}` from\n"
        f"[{TRUST_REPO}](https://github.com/{TRUST_REPO}) at commit `{commit}`, by the\n"
        "Coalition for Content Provenance and Authenticity (C2PA), licensed under\n"
        f"[CC BY 4.0](https://github.com/{TRUST_REPO}/blob/main/LICENSE).\n"
    ).encode()


def vendor_trust_list(client):
    # type: (httpx2.Client) -> None
    """Write the C2PA trust list at its latest commit, with a README that records the source."""
    commits = fetch(client, f"https://api.github.com/repos/{TRUST_REPO}/commits", {"path": TRUST_FILE, "per_page": 1})
    commit = commits.json()[0]["sha"]
    pem = fetch(client, f"https://raw.githubusercontent.com/{TRUST_REPO}/{commit}/{TRUST_FILE}").content
    target = VENDOR / "c2pa-trust-list"
    write(target, "C2PA-TRUST-LIST.pem", pem)
    write(target, "README.md", trust_list_readme(commit))


def main():
    # type: () -> None
    """Vendor all browser assets of the file check."""
    with httpx2.Client(timeout=120, follow_redirects=True) as client:
        vendor_c2pa_web(client)
        vendor_highgain(client)
        vendor_trust_list(client)


if __name__ == "__main__":
    main()
