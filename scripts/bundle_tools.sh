#!/usr/bin/env bash
# Bundle portable copies of the external programs the MCP tools invoke, so that
# release builds do not require the user to install nmap / binutils / ... first.
#
# Layout (consumed by src/utils/resource_finder.py::get_tool_path):
#   libs/tools/<plat>/<arch>/<tool>[.exe]   (+ sibling DLLs / data files)
#
# Only tools that have a reliable official static / portable distribution are
# bundled here. Tools without one (nm, objdump, strings, tshark, traceroute,
# pactl/wpctl/amixer) keep using the system PATH, and the MCP layer reports a
# clear "not installed" message when they are missing.
#
# Usage:
#   ./scripts/bundle_tools.sh              # auto-detect host platform
#   ./scripts/bundle_tools.sh win x64
#   ./scripts/bundle_tools.sh linux x64
#
# Env:
#   NMAP_VERSION     default: 7.991
#   SKIP_SMOKE=1     skip the isolated --version check

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NMAP_VERSION="${NMAP_VERSION:-7.991}"

detect_plat_arch() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Darwin)
      PLAT="mac"
      case "$arch" in
        arm64|aarch64) ARCH="arm64" ;;
        *) ARCH="x64" ;;
      esac
      ;;
    Linux)
      PLAT="linux"
      case "$arch" in
        aarch64|arm64) ARCH="arm64" ;;
        *) ARCH="x64" ;;
      esac
      ;;
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
      PLAT="win"
      ARCH="x64"
      ;;
    *)
      echo "Unsupported OS: $os" >&2
      exit 1
      ;;
  esac
}

if [[ $# -ge 2 ]]; then
  PLAT="$1"
  ARCH="$2"
elif [[ $# -eq 0 ]]; then
  detect_plat_arch
else
  echo "Usage: $0 [plat arch]" >&2
  exit 1
fi

DEST="libs/tools/${PLAT}/${ARCH}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$DEST"
# clear previous inject (keep the directory itself); pure bash so a Windows
# find.exe shadowing GNU find on PATH cannot break this
shopt -s nullglob dotglob
for entry in "$DEST"/*; do
  rm -rf "$entry"
done
shopt -u nullglob dotglob

echo "==> Bundling portable tools for ${PLAT}/${ARCH} -> ${DEST}"

download() {
  local url="$1" out="$2"
  echo "    curl $url"
  curl -fsSL --retry 3 -o "$out" "$url"
}

# ─── 7-Zip (needed to unpack the Windows nmap NSIS installer) ────────────────
find_7z() {
  local candidate
  for candidate in "${SEVENZIP:-}" "7z" "7za"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in \
    "/c/Program Files/7-Zip/7z.exe" \
    "/c/Program Files (x86)/7-Zip/7z.exe" \
    "C:/Program Files/7-Zip/7z.exe" \
    "C:/Program Files (x86)/7-Zip/7z.exe"; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

bundle_nmap_win() {
  # nmap has no official static build; the Windows distribution is an NSIS
  # self-installer. 7z can unpack it, and the payload at the archive root is a
  # self-contained nmap tree (nmap.exe + DLLs + data files).
  local sevenz
  if ! sevenz="$(find_7z)"; then
    echo "ERROR: 7-Zip is required to unpack the Windows nmap installer." >&2
    echo "  It is preinstalled on GitHub's windows runners; locally install" >&2
    echo "  7-Zip or set SEVENZIP=/path/to/7z.exe" >&2
    exit 1
  fi
  echo "    using 7z: $sevenz"

  local url="https://nmap.org/dist/nmap-${NMAP_VERSION}-setup.exe"
  download "$url" "$TMP/nmap-setup.exe"

  mkdir -p "$TMP/nmap"
  # zenmap (the GTK GUI) is the bulk of the archive and is never used here; the
  # "Data Error" lines 7z prints for its compressed payload are expected.
  "$sevenz" x "$TMP/nmap-setup.exe" -o"$TMP/nmap" -y >/dev/null 2>&1 || true

  if [[ ! -f "$TMP/nmap/nmap.exe" ]]; then
    echo "ERROR: nmap.exe not found after extracting $TMP/nmap-setup.exe" >&2
    exit 1
  fi

  # Executables + the DLLs nmap.exe links against.
  cp "$TMP/nmap/nmap.exe" "$DEST/nmap.exe"
  local f
  for f in libcrypto-3.dll libssl-3.dll libssh2.dll zlibwapi.dll ca-bundle.crt; do
    if [[ -f "$TMP/nmap/$f" ]]; then
      cp "$TMP/nmap/$f" "$DEST/$f"
    fi
  done

  # Runtime data files (service / OS / protocol databases).
  for f in nmap-services nmap-protocols nmap-rpc nmap-os-db \
           nmap-service-probes nmap-mac-prefixes; do
    if [[ -f "$TMP/nmap/$f" ]]; then
      cp "$TMP/nmap/$f" "$DEST/$f"
    fi
  done

  # NSE (nmap --script) support.
  for d in nselib scripts; do
    if [[ -d "$TMP/nmap/$d" ]]; then
      cp -r "$TMP/nmap/$d" "$DEST/$d"
    fi
  done

  if [[ "${SKIP_SMOKE:-0}" != "1" ]]; then
    if ! "$DEST/nmap.exe" -V >/dev/null 2>&1; then
      echo "ERROR: bundled nmap.exe failed to run" >&2
      exit 1
    fi
    echo "    smoke OK: nmap.exe ($("$DEST/nmap.exe" -V 2>/dev/null | head -1))"
  fi
}

bundle_linux() {
  # No official static builds exist for the Kali toolchain on Linux; the tools
  # are expected from the system package manager. Nothing to copy here, but the
  # directory is created so the layout is uniform across platforms.
  echo "    no portable Linux tool builds available; relying on system PATH"
}

bundle_mac() {
  # Same situation as Linux: nmap ships as a .dmg (GUI installer), and the Kali
  # toolchain comes from Homebrew/MacPorts, which is not portable.
  echo "    no portable macOS tool builds available; relying on system PATH"
}

case "$PLAT" in
  win)
    bundle_nmap_win
    ;;
  linux)
    bundle_linux
    ;;
  mac)
    bundle_mac
    ;;
  *)
    echo "Unsupported platform: $PLAT" >&2
    exit 1
    ;;
esac

echo "==> Tools bundled into ${DEST}:"
( cd "$DEST" && ls -1 )
