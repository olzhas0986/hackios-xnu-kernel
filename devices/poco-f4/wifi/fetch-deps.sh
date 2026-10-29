#!/bin/bash
# Fetch only SHA-pinned upstream source archives. Run from a networked terminal.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
DEST="$ROOT/out/munch-wifi/downloads"
mkdir -p "$DEST"
fetch() {
    local name=$1 url=$2 digest=$3
    if [ -f "$DEST/$name" ]; then
        echo "$digest  $DEST/$name" | sha256sum -c -
        return
    fi
    curl -fL --retry 3 "$url" -o "$DEST/$name.part"
    echo "$digest  $DEST/$name.part" | sha256sum -c -
    mv "$DEST/$name.part" "$DEST/$name"
}
fetch linux-7.0.14.tar.xz https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.0.14.tar.xz \
    de9999b784d2293f00d39c62d8f92a08ab8a54bc4e80ffd250a0c09cb07a0f98
fetch freebsd-15.1-src.txz https://download.freebsd.org/releases/amd64/15.1-RELEASE/src.txz \
    cf5762da53fd52e1eaf0f9ceee9bf58cbe314c821031d0d9ffa76823185a89e1
printf 'Source archives verified in %s\n' "$DEST"
