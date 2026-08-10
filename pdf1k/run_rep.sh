#!/bin/bash
# One measured rep: clean restart -> samplers on -> prime -> burst -> validate.
# Usage: run_rep.sh <arm: rr|lg> <rep_dir> <n_docs> <timeout_s>
# Watchdog: no per-doc progress (completion or definitive failure) for 10 min
# -> capture diagnostics, kill driver, mark rep invalid, exit 2.
set -u
ARM=$1; REP=$2; N=$3; TIMEOUT=$4
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
PRIME_DOC="000009.pdf"   # fixed, named prime doc — identical both arms
mkdir -p "$REP"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$REP/rep.log"; }

log "rep start arm=$ARM n=$N timeout=${TIMEOUT}s"

# 1. clean restart BOTH containers
docker compose -f "$ROOT/docker-compose.yml" restart >/dev/null 2>&1
for i in $(seq 1 120); do
  lg=$(docker inspect -f '{{.State.Health.Status}}' prodbench-langgraph 2>/dev/null)
  rr=$(docker inspect -f '{{.State.Health.Status}}' prodbench-rocketride 2>/dev/null)
  [ "$lg" = "healthy" ] && [ "$rr" = "healthy" ] && break; sleep 2
done
log "containers healthy: lg=$lg rr=$rr"
[ "$lg" = "healthy" ] && [ "$rr" = "healthy" ] || { log "RESTART FAILED"; exit 3; }

# 2. samplers
sh "$ROOT/pdf1k/host_sampler.sh" > "$REP/host_sampler.jsonl" 2>/dev/null &
HOST_SAMPLER=$!
CONT=$([ "$ARM" = rr ] && echo prodbench-rocketride || echo prodbench-langgraph)
docker cp "$ROOT/pdf1k/proc_sampler.py" "$CONT":/tmp/proc_sampler.py >/dev/null
docker exec "$CONT" python /tmp/proc_sampler.py > "$REP/container_sampler.jsonl" 2>/dev/null &
CONT_SAMPLER=$!
log "samplers on (host=$HOST_SAMPLER container=$CONT_SAMPLER)"

# 3. prime (unmeasured, one fixed doc)
if [ "$ARM" = lg ]; then
  curl -s -o /dev/null -w "prime http=%{http_code}\n" \
    -F "file=@$ROOT/datasets/govdocs/$PRIME_DOC;type=application/pdf" \
    http://localhost:8100/v1/process/document-pdf-v1 | tee -a "$REP/rep.log"
else
  docker exec prodbench-rocketride python /work/send_one.py "/work/corpus/$PRIME_DOC" \
    >> "$REP/rep.log" 2>&1 && log "prime ok" || log "prime FAILED"
fi
log "prime done; burst begins"
date +%s > "$REP/burst_start_epoch"

# 4. burst (driver in background) + watchdog
if [ "$ARM" = lg ]; then
  "$ROOT/langgraph-fastapi/.venv/bin/python" "$ROOT/pdf1k/lg_burst.py" \
    "$ROOT/datasets/govdocs" "$REP/per_doc.jsonl" "$TIMEOUT" "$N" \
    > "$REP/driver.log" 2>&1 &
  DRIVER=$!
  count() { wc -l < "$REP/per_doc.jsonl" 2>/dev/null | tr -d ' '; }
else
  docker exec prodbench-rocketride mkdir -p /work/rep >/dev/null 2>&1
  docker exec prodbench-rocketride sh -c "rm -f /work/rep/per_doc.jsonl" >/dev/null 2>&1
  docker cp "$ROOT/pdf1k/rr_burst.py" prodbench-rocketride:/work/rr_burst.py >/dev/null
  docker exec prodbench-rocketride python /work/rr_burst.py \
    /work/corpus /work/rep/per_doc.jsonl "$TIMEOUT" "$N" \
    > "$REP/driver.log" 2>&1 &
  DRIVER=$!
  count() { docker exec prodbench-rocketride sh -c 'wc -l < /work/rep/per_doc.jsonl' 2>/dev/null | tr -d ' '; }
fi

LAST=0; STALL=0; INVALID=0
while kill -0 $DRIVER 2>/dev/null; do
  sleep 30
  NOW=$(count); NOW=${NOW:-0}
  if [ "$NOW" = "$LAST" ]; then
    STALL=$((STALL+30))
    if [ $STALL -ge 600 ]; then
      log "WATCHDOG: no progress for 10 min at $NOW/$N — killing rep"
      docker logs --tail 100 "$CONT" > "$REP/container_tail.log" 2>&1
      tail -50 "$REP/driver.log" > "$REP/driver_tail.log" 2>/dev/null
      kill $DRIVER 2>/dev/null
      [ "$ARM" = rr ] && docker exec prodbench-rocketride sh -c 'pkill -f rr_burst.py' 2>/dev/null
      INVALID=1; break
    fi
  else
    STALL=0; LAST=$NOW
  fi
done
wait $DRIVER 2>/dev/null; DRIVER_RC=$?
date +%s > "$REP/burst_end_epoch"

# 5. teardown samplers; collect RR results to host
kill $HOST_SAMPLER $CONT_SAMPLER 2>/dev/null
[ "$ARM" = rr ] && docker cp prodbench-rocketride:/work/rep/per_doc.jsonl "$REP/per_doc.jsonl" >/dev/null 2>&1
FINAL=$(wc -l < "$REP/per_doc.jsonl" 2>/dev/null | tr -d ' ')
log "burst over: rc=$DRIVER_RC records=$FINAL invalid_by_watchdog=$INVALID"

# 6. validate
"$ROOT/langgraph-fastapi/.venv/bin/python" "$ROOT/pdf1k/validate_rep.py" \
  "$REP" "$ARM" "$N" > "$REP/validation.json" 2>"$REP/validation.err"
log "validation: $(cat "$REP/validation.json" 2>/dev/null | head -c 300)"
[ $INVALID -eq 1 ] && { echo '{"valid": false, "reason": "watchdog"}' > "$REP/validity_override.json"; exit 2; }
exit 0
