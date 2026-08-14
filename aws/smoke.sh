#!/bin/bash
# LangGraph-arm smoke test for the benchmark box (Ubuntu, ssm-user, no sudo).
# Clone/pull repo -> build+start the LangGraph service -> send real Govdocs
# PDFs through the endpoint -> verify responses -> summary (+ optional S3).
#
#   bash bench/aws/smoke.sh          (or run from anywhere; paths are absolute)
set -u
REPO="https://github.com/Leela8256/bench_langgraph_prod.git"
BENCH="$HOME/bench"
SMOKE="$HOME/smoke"
DOCS="000009 000010 000011 000012 000013"   # known-good govdocs, first of corpus
ZIP_URL="https://digitalcorpora.s3.amazonaws.com/corpora/files/govdocs1/zipfiles/000.zip"
say() { echo "[smoke $(date +%H:%M:%S)] $*"; }

say "1/6 repo"
if [ -d "$BENCH/.git" ]; then git -C "$BENCH" pull -q; else git clone -q "$REPO" "$BENCH"; fi
say "repo at $(git -C "$BENCH" rev-parse --short HEAD)"

say "2/6 build + start langgraph (first build pulls torch — several minutes)"
cd "$BENCH"
docker compose up -d --build langgraph 2>&1 | tail -2

say "3/6 wait for warmup-gated readiness"
for i in $(seq 1 180); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8100/health/ready 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 5
done
[ "$code" = "200" ] || { say "FAIL: service never became ready"; docker logs --tail 30 prodbench-langgraph; exit 1; }
say "ready. /meta extractor: $(curl -s http://localhost:8100/meta | grep -o '"mode":"[a-z]*"' | head -1)"

say "4/6 fetch govdocs smoke docs (zip pulled on-box; extracting ${DOCS})"
mkdir -p "$SMOKE/docs" "$SMOKE/out"
if [ ! -f "$SMOKE/000.zip" ]; then curl -fsSL "$ZIP_URL" -o "$SMOKE/000.zip"; fi
for d in $DOCS; do unzip -o -j -q "$SMOKE/000.zip" "*${d}.pdf" -d "$SMOKE/docs" 2>/dev/null; done
say "extracted: $(ls "$SMOKE/docs" | wc -l) pdfs"

say "5/6 send docs through the endpoint"
PASS=0; FAIL=0
for pdf in "$SMOKE/docs"/*.pdf; do
  name=$(basename "$pdf")
  hdr=$(mktemp); body="$SMOKE/out/${name%.pdf}.json"
  code=$(curl -s -D "$hdr" -o "$body" -w '%{http_code}' \
    -F "file=@$pdf;type=application/pdf" -F "request_id=smoke-${name%.pdf}" \
    http://localhost:8100/v1/process/document-pdf-v1)
  claimed=$(tr -d '\r' < "$hdr" | awk 'tolower($1)=="x-output-sha256:"{print $2}')
  actual=$(sha256sum "$body" | cut -d' ' -f1)
  chunks=$(python3 -c "import json;print(len(json.load(open('$body'))['output']['chunks']))" 2>/dev/null || echo "?")
  vecs=$(python3 -c "import json;o=json.load(open('$body'))['output'];print(len(o['vectors'][0]) if o['vectors'] else 0)" 2>/dev/null || echo "?")
  if [ "$code" = "200" ] && [ "$claimed" = "$actual" ]; then
    say "  $name: 200, sha OK, chunks=$chunks, dim=$vecs"; PASS=$((PASS+1))
  else
    say "  $name: FAIL http=$code sha_match=$([ "$claimed" = "$actual" ] && echo yes || echo NO)"; FAIL=$((FAIL+1))
  fi
  rm -f "$hdr"
done

say "6/6 summary: $PASS pass / $FAIL fail"
{ echo "smoke $(date -u +%FT%TZ) commit=$(git -C "$BENCH" rev-parse --short HEAD) pass=$PASS fail=$FAIL"; } \
  >> "$SMOKE/out/SUMMARY.txt"
aws s3 cp "$SMOKE/out/" "s3://rocketride-benchmark-data/leela/smoke/" --recursive --quiet 2>/dev/null \
  && say "results uploaded to s3://rocketride-benchmark-data/leela/smoke/" \
  || say "s3 upload skipped/failed (fine for smoke)"
[ "$FAIL" = "0" ] && say "SMOKE PASS" || { say "SMOKE FAIL"; exit 1; }
