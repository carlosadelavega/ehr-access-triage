#!/usr/bin/env bash
# Download the pinned Synthea release into vendor/ and verify its checksum.
# The jar is about 117 MB and is never committed.
set -euo pipefail
VERSION="v3.2.0"
SHA256="57216a99a4ffc12450e4aba28f9d42e1678abb3b31ec7cd5ac2c4014e9db0720"
DEST="$(cd "$(dirname "$0")/.." && pwd)/vendor"
JAR="$DEST/synthea-with-dependencies.jar"
mkdir -p "$DEST"
if [ ! -f "$JAR" ]; then
  curl -L --fail -o "$JAR" \
    "https://github.com/synthetichealth/synthea/releases/download/$VERSION/synthea-with-dependencies.jar"
fi
if command -v sha256sum >/dev/null; then
  ACTUAL="$(sha256sum "$JAR" | cut -d' ' -f1)"
else
  ACTUAL="$(shasum -a 256 "$JAR" | cut -d' ' -f1)"
fi
if [ "$ACTUAL" != "$SHA256" ]; then
  echo "Checksum mismatch for $JAR" >&2
  exit 1
fi
echo "Synthea $VERSION ready at $JAR"
