#!/usr/bin/env bash
# Run the C2PA Soft Binding API conformance harness against a running resolver.
#
# Usage: scripts/conformance.sh [API base URL, default http://127.0.0.1:45460/v1]
#
# The npm package of harness 1.1.0 lacks the compiled CLI (dist/), so this script installs the package into a
# temporary directory and runs its tests with Jest directly. CONFORMANCE_DISCOVERY=true makes the checks of the
# C2PA 2.5 service routes run whatever the caller's environment says.
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:45460/v1}"
HARNESS="@cognitiveproof/softbinding-api-conformance@1.1.0"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
npm init -y >/dev/null
npm install --silent --no-audit --no-fund "$HARNESS"
CONFIG=node_modules/@cognitiveproof/softbinding-api-conformance/jest.conformance.config.js

CONFORMANCE_CAPABILITIES="$(curl -sf "$BASE_URL/services/capabilities")"
export CONFORMANCE_BASE_URL="$BASE_URL" CONFORMANCE_TOKEN=unused CONFORMANCE_CAPABILITIES CONFORMANCE_DISCOVERY=true
npx jest --config "$CONFIG" --verbose
