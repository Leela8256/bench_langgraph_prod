#!/usr/bin/env bash
# Smoke corpus: the FIRST N govdocs PDFs, fetched on the box (no scp path).
#
# Govdocs, not arXiv, and from the same digitalcorpora zip aws/smoke.sh uses:
# every local result (gate-50, pdf200/500/1k) is govdocs, and gate-50 takes
# sorted(*.pdf)[:N] from the same corpus. Matching it is what makes the box
# numbers comparable to the Mac numbers instead of a separate universe.
#
# Extraction uses python3's zipfile, NOT unzip: the box has no unzip and no
# sudo to install it (preflight flags this). python3 is required anyway --
# the driver is python.
#
#   bash aws_run/box/fetch_smoke_corpus.sh [dest_dir] [n]
set -euo pipefail
DEST="${1:-$HOME/smoke_corpus}"
N="${2:-10}"
ZIP_URL="https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles/000.zip"
CACHE="$HOME/govdocs_000.zip"

mkdir -p "$DEST"
if [ ! -s "$CACHE" ]; then
  echo "downloading govdocs 000.zip (one archive, several hundred MB; cached)"
  curl -fsSL --max-time 1800 "$ZIP_URL" -o "$CACHE"
fi
echo "zip: $(du -h "$CACHE" | cut -f1)"

rm -f "$DEST"/*.pdf
python3 - "$CACHE" "$DEST" "$N" <<'PY'
import sys, zipfile, pathlib

cache, dest, n = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3])
with zipfile.ZipFile(cache) as z:
    # Sort by BASENAME so the selection matches gate-50's sorted(*.pdf)[:N]
    # regardless of how the archive nests paths.
    pdfs = sorted((i for i in z.infolist()
                   if i.filename.lower().endswith(".pdf") and not i.is_dir()),
                  key=lambda i: pathlib.PurePosixPath(i.filename).name)
    if len(pdfs) < n:
        sys.exit(f"FATAL: only {len(pdfs)} PDFs in archive, need {n}")
    for info in pdfs[:n]:
        name = pathlib.PurePosixPath(info.filename).name
        (dest / name).write_bytes(z.read(info))
        print(f"  {name}  {info.file_size:,} bytes")
PY

cd "$DEST"
sha256sum *.pdf > SHA256SUMS
echo
echo "corpus ready: $DEST ($(ls -1 *.pdf | wc -l | tr -d ' ') docs)"
cat SHA256SUMS
