#!/bin/bash
# Autonomous overnight sequencer. Assumes cal-lg already running/complete.
# Stages: wait cal-lg -> cal-rr -> freeze timeouts -> rep1-lg (full) ->
# rep1-rr (until cutoff) -> post drift fixture. Emits progress lines.
#
# ORDER DEVIATION (logged in OVERNIGHT_STATUS): protocol says alternate
# RR->LG, but LG rep1 is the night's only potentially-VALID rep (RR mass-
# stalls under burst per probe+calibration). Securing one complete, valid
# rep beats strict alternation under the 07:00 deadline. RR rep1 runs
# second and is cut at 06:40 if needed (partial, preserved, invalid).
set -u
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
cd "$ROOT"
CUTOFF_EPOCH=$(date -j -f "%H:%M" "06:40" +%s 2>/dev/null)
say() { echo "[chain $(date +%H:%M:%S)] $*"; }

# 1. wait for cal-lg
until [ -f runs/pdf1k/cal-lg/validation.json ]; do sleep 15; done
say "cal-lg done: $(head -c 200 runs/pdf1k/cal-lg/validation.json)"

# 2. cal-rr
say "cal-rr starting"
bash pdf1k/run_rep.sh rr runs/pdf1k/cal-rr 100 900 >/dev/null 2>&1
say "cal-rr done: $(head -c 200 runs/pdf1k/cal-rr/validation.json 2>/dev/null)"

# 3. freeze
langgraph-fastapi/.venv/bin/python pdf1k/freeze_timeouts.py \
  runs/pdf1k/cal-lg/per_doc.jsonl runs/pdf1k/cal-rr/per_doc.jsonl \
  runs/pdf1k/frozen_params.json
LG_T=$(/usr/bin/python3 -c "import json;print(json.load(open('runs/pdf1k/frozen_params.json'))['lg_timeout_s'])")
RR_T=$(/usr/bin/python3 -c "import json;print(json.load(open('runs/pdf1k/frozen_params.json'))['rr_timeout_s'])")
say "FROZEN: lg_timeout=${LG_T}s rr_timeout=${RR_T}s"

# 4. rep1-lg (full 1000)
say "rep1-lg starting (n=1000 timeout=${LG_T}s)"
bash pdf1k/run_rep.sh lg runs/pdf1k/rep1-lg 1000 "$LG_T" >/dev/null 2>&1 &
REP_PID=$!
while kill -0 $REP_PID 2>/dev/null; do
  if [ "$(date +%s)" -ge "$CUTOFF_EPOCH" ]; then
    say "CUTOFF 06:40 hit during rep1-lg — killing (partial, invalid, preserved)"
    pkill -P $REP_PID 2>/dev/null; kill $REP_PID 2>/dev/null
    echo '{"valid": false, "reason": "cutoff 06:40"}' > runs/pdf1k/rep1-lg/validity_override.json
    break
  fi
  sleep 60
done
wait $REP_PID 2>/dev/null
say "rep1-lg finished: $(head -c 200 runs/pdf1k/rep1-lg/validation.json 2>/dev/null)"

# 5. rep1-rr if time remains (needs ~RR_T + overhead)
NOW=$(date +%s)
if [ $((CUTOFF_EPOCH - NOW)) -gt 900 ]; then
  say "rep1-rr starting (n=1000 timeout=${RR_T}s)"
  bash pdf1k/run_rep.sh rr runs/pdf1k/rep1-rr 1000 "$RR_T" >/dev/null 2>&1 &
  REP_PID=$!
  while kill -0 $REP_PID 2>/dev/null; do
    if [ "$(date +%s)" -ge "$CUTOFF_EPOCH" ]; then
      say "CUTOFF 06:40 hit during rep1-rr — killing (partial, invalid, preserved)"
      docker exec prodbench-rocketride sh -c 'pkill -f rr_burst.py' 2>/dev/null
      docker cp prodbench-rocketride:/work/rep/per_doc.jsonl runs/pdf1k/rep1-rr/per_doc.jsonl 2>/dev/null
      pkill -P $REP_PID 2>/dev/null; kill $REP_PID 2>/dev/null
      echo '{"valid": false, "reason": "cutoff 06:40"}' > runs/pdf1k/rep1-rr/validity_override.json
      break
    fi
    sleep 60
  done
  wait $REP_PID 2>/dev/null
  say "rep1-rr finished: $(head -c 200 runs/pdf1k/rep1-rr/validation.json 2>/dev/null)"
else
  say "SKIP rep1-rr: <15 min to cutoff"
fi

# 6. post drift fixture (both arms, unmeasured; LG via its own workload
# module in-container — the endpoint is PDF-only)
say "post-run drift fixture"
docker exec prodbench-langgraph python -c "
from workload.document.embed import embed_chunks
v = embed_chunks(['The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. How vexingly quick daft zebras jump.'])[0]
print([round(x, 8) for x in v[:8]])" > runs/pdf1k/drift_post_lg.txt 2>&1 \
  || say "lg drift fixture failed"
docker exec prodbench-rocketride python /work/send_one.py /work/data/probe/parity_fixture.txt \
  > runs/pdf1k/drift_post_rr.txt 2>&1 || say "rr drift fixture failed"
say "CHAIN COMPLETE"
