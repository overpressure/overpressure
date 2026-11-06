#!/bin/sh
# SPDX-License-Identifier: Unlicense
# Start the overpressure installer on Linux (and macOS, through Install.command): sh install.sh
#
# It fetches the pinned uv into cache/uv (checked by its SHA-256), which fetches Python 3.12 (with
# Tk, which a system Python often lacks) and CustomTkinter into cache/ and opens the window.
# Everything stays inside this folder: nothing is installed on the computer.
set -eu

UV_VERSION=0.13.4
RELEASES=https://github.com/astral-sh/uv/releases/download

# A failing start keeps the terminal open with its error.
finish() {
    status=$?
    if [ "$status" -ne 0 ]; then
        printf '\nThe installer could not start or stopped with an error (above).\n' >&2
        if [ -t 0 ]; then
            printf 'Press Enter to close this window. ' >&2
            read -r _ || true
        fi
    fi
}
trap finish EXIT

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

cd "$(dirname "$0")"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64 | Linux-amd64)
        asset="uv-x86_64-unknown-linux-musl"
        sum=28d4385f9f9d1d1c7654d85b3855e0e2615b2f6cc13136a2edff858bbdc16ff1
        ;;
    Linux-aarch64 | Linux-arm64)
        asset="uv-aarch64-unknown-linux-musl"
        sum=253a17cadcb659ae77a0fe3b9a2f1c14297528d480c421899e61e64d64f24e8f
        ;;
    Darwin-x86_64)
        asset="uv-x86_64-apple-darwin"
        sum=7be2dcd316122f4b3e5a0a7889f6248266c14425374e105ff7dc9779184603a6
        ;;
    Darwin-arm64)
        asset="uv-aarch64-apple-darwin"
        sum=e0b1d4743ce64c55d9f0f4a444afa6b81125680070ee04e853fcd0f6c1ebf8f1
        ;;
    *) fail "no uv for this computer ($(uname -s) $(uname -m)): python3 -m overpressure install does the same in a terminal" ;;
esac

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fL --proto '=https' --tlsv1.2 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -O "$2" "$1"; }
else
    fail "curl or wget is needed to download uv"
fi
if command -v sha256sum >/dev/null 2>&1; then
    hash_of() { sha256sum "$1" | cut -d ' ' -f 1; }
elif command -v shasum >/dev/null 2>&1; then
    hash_of() { shasum -a 256 "$1" | cut -d ' ' -f 1; }
else
    fail "sha256sum or shasum is needed to check uv"
fi

archive="cache/uv/$asset.tar.gz"
folder="cache/uv/$UV_VERSION"
uv="$folder/$asset/uv"
if [ ! -f "$archive" ] || [ "$(hash_of "$archive")" != "$sum" ]; then
    mkdir -p cache/uv
    printf 'Downloading uv %s ...\n' "$UV_VERSION"
    fetch "$RELEASES/$UV_VERSION/$asset.tar.gz" "$archive"
    if [ "$(hash_of "$archive")" != "$sum" ]; then
        rm -f "$archive"
        fail "the downloaded uv does not match its pinned SHA-256, so it was deleted. Try again later."
    fi
    rm -rf "$folder"
fi
if [ ! -x "$uv" ]; then
    mkdir -p "$folder"
    tar -xzf "$archive" -C "$folder"
fi

UV_CACHE_DIR="$PWD/cache/uv-cache"
UV_PYTHON_INSTALL_DIR="$PWD/cache/python"
UV_PROJECT_ENVIRONMENT="$PWD/cache/venv"
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_PROJECT_ENVIRONMENT
"$uv" run --locked --no-dev --extra gui --managed-python --python 3.12 python -m overpressure gui
