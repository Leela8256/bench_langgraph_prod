#!/usr/bin/env bash
# LOCAL (Mac): hash every dataset file → aws_run/MANIFEST.sha256 (committed).
# The box verifies its copy byte-for-byte against this, whichever transfer
# path (S3 upload vs re-download) wins. Checklist 0.2.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
[ -d datasets ] || { echo "no datasets/ at $ROOT"; exit 1; }
find datasets -type f ! -name '.DS_Store' -print0 | sort -z \
  | xargs -0 shasum -a 256 > aws_run/MANIFEST.sha256
echo "hashed $(wc -l < aws_run/MANIFEST.sha256 | tr -d ' ') files → aws_run/MANIFEST.sha256"
