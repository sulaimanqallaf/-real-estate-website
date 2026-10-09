#!/bin/bash
# Downloads the toxiproxy-server binary for REAL network-fault-injection
# testing (Sprint 3, Reliability milestone) - tests/test_fault_injection_
# network.py's Toxiproxy-based tests SKIP cleanly when this binary isn't
# on PATH, so running this script is entirely optional. It is never
# required to run the rest of this project's test suite.
#
# Downloaded to .bin/ (gitignored, never committed - same discipline as
# .venvs/). Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$REPO_ROOT/.bin"
VERSION="2.9.0"

mkdir -p "$BIN_DIR"

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS-$ARCH" in
  Linux-x86_64) ASSET="toxiproxy-server-linux-amd64" ;;
  Linux-aarch64) ASSET="toxiproxy-server-linux-arm64" ;;
  Darwin-x86_64) ASSET="toxiproxy-server-darwin-amd64" ;;
  Darwin-arm64) ASSET="toxiproxy-server-darwin-arm64" ;;
  *) echo "Unsupported platform: $OS-$ARCH - see https://github.com/Shopify/toxiproxy/releases to download manually." >&2; exit 1 ;;
esac

TARGET="$BIN_DIR/toxiproxy-server"
if [ -x "$TARGET" ]; then
  echo "toxiproxy-server already present at $TARGET"
else
  URL="https://github.com/Shopify/toxiproxy/releases/download/v${VERSION}/${ASSET}"
  echo "Downloading toxiproxy-server ${VERSION} for ${OS}-${ARCH}..."
  curl -sSL -o "$TARGET" "$URL"
  chmod +x "$TARGET"
fi

"$TARGET" --version
echo
echo "Done. tests/test_fault_injection_network.py will now find it at $TARGET"
echo "(it also checks the PATH and TOXIPROXY_SERVER_PATH env var)."
