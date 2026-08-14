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
BASE="https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles"

# One archive holds ~1000 mixed-type files, of which only a fraction are PDFs,
# so larger N may span several. Archives are consumed in order and cached, and
# selection stays sorted-by-basename across all of them -- the same rule
# gate-50 uses, so doc identity is stable no matter how many archives it took.
mkdir -p "$DEST"
rm -f "$DEST"/*.pdf
CACHES=()
for idx in 000 001 002 003 004; do
  CACHE="$HOME/govdocs_${idx}.zip"
  if [ ! -s "$CACHE" ]; then
    echo "downloading govdocs ${idx}.zip (cached after first use)"
    curl -fsSL --max-time 1800 "$BASE/${idx}.zip" -o "$CACHE"
  fi
  CACHES+=("$CACHE")
  have=$(python3 - "$N" "${CACHES[@]}" <<'PY'
import sys, zipfile, pathlib
n, caches = int(sys.argv[1]), sys.argv[2:]
seen = set()
for c in caches:
    with zipfile.ZipFile(c) as z:
        seen.update(pathlib.PurePosixPath(i.filename).name
                    for i in z.infolist()
                    if i.filename.lower().endswith(".pdf") and not i.is_dir())
print(len(seen))
PY
)
  echo "  archives so far: ${#CACHES[@]}, PDFs available: $have (need $N)"
  [ "$have" -ge "$N" ] && break
done

python3 - "$DEST" "$N" "${CACHES[@]}" <<'PY'
import sys, zipfile, pathlib
dest, n, caches = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]
found = {}
for c in caches:
    with zipfile.ZipFile(c) as z:
        for i in z.infolist():
            if i.filename.lower().endswith(".pdf") and not i.is_dir():
                name = pathlib.PurePosixPath(i.filename).name
                found.setdefault(name, (c, i))
if len(found) < n:
    sys.exit(f"FATAL: only {len(found)} PDFs across {len(caches)} archives, need {n}")
for name in sorted(found)[:n]:
    c, info = found[name]
    with zipfile.ZipFile(c) as z:
        (dest / name).write_bytes(z.read(info))
print(f"extracted {n} PDFs from {len(caches)} archive(s)")
PY

cd "$DEST"
sha256sum *.pdf > SHA256SUMS
echo
echo "corpus ready: $DEST ($(ls -1 *.pdf | wc -l | tr -d ' ') docs)"
cat SHA256SUMS
