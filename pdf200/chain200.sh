#!/bin/bash
# PDF-200 autonomous chain: probe -> calibration -> freeze -> predictions
# gate check -> reps (alternating arm order, levels 1,4,16,64 ascending).
# Clean container restart + full warmup before each (rep, arm) block.
set -u
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
cd "$ROOT"
R="runs/pdf200"
LEVELS="1 4 16 64"
N=200
REPS=${REPS:-3}
say() { echo "[chain200 $(date +%H:%M:%S)] $*"; }

restart_and_warm() { # $1 = arm to warm (both containers restart)
  docker compose restart >/dev/null 2>&1
  for i in $(seq 1 120); do
    lg=$(docker inspect -f '{{.State.Health.Status}}' prodbench-langgraph 2>/dev/null)
    rr=$(docker inspect -f '{{.State.Health.Status}}' prodbench-rocketride 2>/dev/null)
    [ "$lg" = "healthy" ] && [ "$rr" = "healthy" ] && break; sleep 2
  done
  if [ "$1" = lg ]; then
    curl -s -o /dev/null -F "file=@$ROOT/datasets/govdocs/000009.pdf;type=application/pdf" \
      http://localhost:8100/v1/process/document-pdf-v1
  else
    docker exec prodbench-rocketride sh -c 'mkdir -p /work/out/warm' >/dev/null
    docker exec prodbench-rocketride python /work/rr_stepped.py \
      /work/corpus /work/out/warm 1 120 1 --warmup-only >/dev/null 2>&1
  fi
}

start_samplers() { # $1 arm  $2 outdir
  sh "$ROOT/pdf1k/host_sampler.sh" > "$2/host_sampler.jsonl" 2>/dev/null &
  echo $! > "$2/.host_sampler_pid"
  C=$([ "$1" = rr ] && echo prodbench-rocketride || echo prodbench-langgraph)
  docker cp "$ROOT/pdf1k/proc_sampler.py" "$C":/tmp/proc_sampler.py >/dev/null
  docker exec "$C" python /tmp/proc_sampler.py > "$2/container_sampler.jsonl" 2>/dev/null &
  echo $! > "$2/.cont_sampler_pid"
}
stop_samplers() { # $1 outdir
  kill "$(cat "$1/.host_sampler_pid" 2>/dev/null)" 2>/dev/null
  kill "$(cat "$1/.cont_sampler_pid" 2>/dev/null)" 2>/dev/null
}

LG_T=$(/usr/bin/python3 -c "import json;print(json.load(open('$R/frozen_params.json'))['lg_timeout_s'])")
RR_T=$(/usr/bin/python3 -c "import json;print(json.load(open('$R/frozen_params.json'))['rr_timeout_s'])")
say "frozen timeouts: lg=$LG_T rr=$RR_T"

for rep in $(seq 1 "$REPS"); do
  if [ $((rep % 2)) -eq 1 ]; then ORDER="rr lg"; else ORDER="lg rr"; fi
  for arm in $ORDER; do
    T=$([ "$arm" = lg ] && echo "$LG_T" || echo "$RR_T")
    BLOCK="$R/rep$rep-$arm"
    mkdir -p "$BLOCK"
    say "rep$rep $arm: restart+warmup"
    restart_and_warm "$arm"
    start_samplers "$arm" "$BLOCK"
    date +%s > "$BLOCK/block_start_epoch"
    for L in $LEVELS; do
      say "rep$rep $arm L$L starting"
      bash pdf200/run_level200.sh "$arm" "$L" "$BLOCK/L$L" "$T" "$N"
      say "rep$rep $arm L$L: $(head -c 160 "$BLOCK/L$L/validation.json" 2>/dev/null)"
    done
    date +%s > "$BLOCK/block_end_epoch"
    stop_samplers "$BLOCK"
  done
  say "rep$rep complete"
done

say "post-run drift fixtures"
docker exec prodbench-langgraph python -c "
from workload.document.embed import embed_chunks
v = embed_chunks(['The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. How vexingly quick daft zebras jump.'])[0]
print([round(x, 8) for x in v[:8]])" > "$R/drift_post_lg.txt" 2>&1
docker exec prodbench-rocketride python /work/send_one.py /work/data/probe/parity_fixture.txt \
  > "$R/drift_post_rr.txt" 2>&1
say "CHAIN200 COMPLETE"
