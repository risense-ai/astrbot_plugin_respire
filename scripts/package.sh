#!/usr/bin/env bash
# astrbot_plugin_respire packaging -- CI and local use the same recipe.
# Spec: zip root = plugin root (AstrBot installs data/plugins/<name>/ straight from the zip root; metadata.yaml must sit at the root).
# Produces dist/astrbot_plugin_respire-<version>-<yyyymmdd>.zip with the build baseline commit as the zip comment.
# stdout is exactly two lines: the zip path, then the sha256.
# macOS bash 3.2 compatible: always brace variable expansions as ${VAR} and never place CJK punctuation next to them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VER="$(python3 -c "import re; print(re.search(r'^version:\s*(\S+)', open('metadata.yaml').read(), re.M).group(1))")"
REV="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
STAMP="$(date +%Y%m%d)"
OUT="dist/astrbot_plugin_respire-${VER}-${STAMP}.zip"

mkdir -p dist
rm -f "$OUT"

zip -r "$OUT" \
  main.py metadata.yaml _conf_schema.json requirements.txt logo.png \
  README.md LICENSE CHANGELOG.md \
  -x '*.DS_Store' '*.pyc' >/dev/null

printf '%s\n' "$REV" | zip -z "$OUT" >/dev/null

echo "$OUT"
python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" "$OUT"
