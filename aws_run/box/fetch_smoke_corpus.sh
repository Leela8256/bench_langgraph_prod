#!/usr/bin/env bash
# Smoke corpus: the FIRST N govdocs PDFs, fetched on the box (no scp path).
#
# Govdocs, not arXiv, and from the same digitalcorpora zip aws/smoke.sh uses:
# every local result (gate-50, pdf200/500/1k) is govdocs, and gate-50 takes
# sorted(*.pdf)[:N] from the same corpus. Matching it is what makes the box
# numbers comparable to the Mac numbers instead of a separate universe.
#
#   bash aws_run/box/fetch_smoke_corpus.sh [dest_dir] [n]
set -euo pipefail
DEST="${1:-$HOME/smoke_corpus}"
N="${2:-10}"
ZIP_URL="https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles/000.zip"
CACHE="$HOME/govdocs_000.zip"

command -v unzip >/dev/null || {
  echo "FATAL: unzip not installed and there is no sudo on this box." >&2
  echo "Use python3 instead:  python3 -c \"import zipfile;zipfile.ZipFile('$CACHE').extractall('/tmp/gd')\"" >&2
  exit 1; }

mkdir -p "$DEST"
if [ ! -s "$CACHE" ]; then
  echo "downloading govdocs 000.zip (one archive, several hundred MB; cached)"
  curl -fsSL --max-time 1800 "$ZIP_URL" -o "$CACHE"
fi
echo "zip: $(du -h "$CACHE" | cut -f1)"

# Extract every PDF, then keep the first N in sorted order — the same
# selection rule gate-50 uses, so doc identity matches across hosts.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# unzip exits non-zero on mere warnings; the count check below is the real
# gate, so do not let set -e abort on a warning.
unzip -o -j -q "$CACHE" '*.pdf' -d "$TMP" || true
have=$(find "$TMP" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: only $have PDFs in zip, need $N" >&2; exit 1; }

rm -f "$DEST"/*.pdf
i=0
for f in $(find "$TMP" -name '*.pdf' -printf '%f\n' | sort); do
  cp "$TMP/$f" "$DEST/$f"
  i=$((i + 1))
  [ "$i" -ge "$N" ] && break
done

cd "$DEST"
sha256sum *.pdf > SHA256SUMS
echo
echo "corpus ready: $DEST ($i docs)"
cat SHA256SUMS
