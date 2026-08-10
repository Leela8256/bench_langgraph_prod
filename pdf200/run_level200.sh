#!/bin/bash
# One (arm, level) within a rep. Host-side watchdog on checkpoint MTIME —
# never docker exec polls (the PDF-1K lesson). For RR the in-container
# checkpoint is live-streamed to the host via one long-lived exec stream.
# Usage: run_level200.sh <arm> <level> <out_dir> <timeout_s> <n_docs>
set -u
ARM=$1; LEVEL=$2; OUT=$3; TIMEOUT=$4; N=$5
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
mkdir -p "$OUT"
log() { echo "[$(date +%H:%M:%S)] L$LEVEL $ARM: $*" | tee -a "$OUT/level.log"; }

if [ "$ARM" = lg ]; then
  "$ROOT/langgraph-fastapi/.venv/bin/python" "$ROOT/pdf200/lg_stepped.py" \
    "$ROOT/datasets/govdocs" "$OUT" "$LEVEL" "$TIMEOUT" "$N" \
    > "$OUT/driver.log" 2>&1 &
  DRIVER=$!
  WATCH_FILE="$OUT/per_doc.jsonl"
else
  CDIR="/work/out/$(basename "$(dirname "$OUT")")_L$LEVEL"
  docker exec prodbench-rocketride sh -c "mkdir -p $CDIR"
  docker exec prodbench-rocketride python /work/rr_stepped.py \
    /work/corpus "$CDIR" "$LEVEL" "$TIMEOUT" "$N" \
    > "$OUT/driver.log" 2>&1 &
  DRIVER=$!
  # live mirror: single long-lived stream (not repeated polls)
  ( docker exec prodbench-rocketride sh -c "touch $CDIR/per_doc.jsonl; tail -F $CDIR/per_doc.jsonl" \
      > "$OUT/per_doc.jsonl" 2>/dev/null ) &
  MIRROR=$!
  WATCH_FILE="$OUT/per_doc.jsonl"
fi

STALL=0
while kill -0 $DRIVER 2>/dev/null; do
  sleep 30
  if [ -f "$WATCH_FILE" ]; then
    AGE=$(( $(date +%s) - $(stat -f %m "$WATCH_FILE" 2>/dev/null || echo 0) ))
  else
    AGE=$((AGE + 30))
  fi
  if [ "$AGE" -ge 600 ]; then
    log "WATCHDOG: checkpoint stale ${AGE}s — killing level"
    docker logs --tail 60 prodbench-$([ "$ARM" = rr ] && echo rocketride || echo langgraph) \
      > "$OUT/container_tail.log" 2>&1
    kill $DRIVER 2>/dev/null
    [ "$ARM" = rr ] && docker exec prodbench-rocketride sh -c 'pkill -f rr_stepped.py' 2>/dev/null
    echo '{"valid": false, "reason": "watchdog_stale_checkpoint"}' > "$OUT/validity_override.json"
    break
  fi
done
wait $DRIVER 2>/dev/null; RC=$?
if [ "$ARM" = rr ]; then
  sleep 2; kill $MIRROR 2>/dev/null
  docker cp "prodbench-rocketride:$CDIR/per_doc.jsonl" "$OUT/per_doc.jsonl" >/dev/null 2>&1
  docker cp "prodbench-rocketride:$CDIR/raw" "$OUT/raw" >/dev/null 2>&1
  docker cp "prodbench-rocketride:$CDIR/warmup.json" "$OUT/warmup.json" >/dev/null 2>&1
fi
"$ROOT/langgraph-fastapi/.venv/bin/python" "$ROOT/pdf200/validate200.py" \
  "$OUT" "$ARM" "$N" > "$OUT/validation.json" 2>"$OUT/validation.err"
log "done rc=$RC valid=$(/usr/bin/python3 -c "import json;print(json.load(open('$OUT/validation.json')).get('valid'))" 2>/dev/null)"
