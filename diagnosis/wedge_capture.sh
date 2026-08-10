#!/bin/bash
# Reproduce the wedge under instrumentation and capture deep diagnostics.
# Runs rr_stepped c4 n=50 (known wedge config), watches the in-container
# checkpoint; on 90s stall: /proc thread forensics + SIGQUIT JVM dump.
set -u
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
OUT="$ROOT/runs/diagnosis/${DIAG_TAG:-run}"
mkdir -p "$OUT"
log() { echo "[diag $(date +%H:%M:%S)] $*" | tee -a "$OUT/capture.log"; }

log "clean restart"
docker compose -f "$ROOT/docker-compose.yml" restart rocketride >/dev/null 2>&1
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' prodbench-rocketride 2>/dev/null)
  [ "$s" = healthy ] && break; sleep 2
done
log "rr healthy; launching ${DIAG_CORPUS} c${DIAG_LEVEL} x${DIAG_N} (timeout 120s)"
docker exec prodbench-rocketride sh -c 'mkdir -p /work/out/diag && rm -f /work/out/diag/per_doc.jsonl'
docker exec prodbench-rocketride python /work/rr_stepped.py ${DIAG_CORPUS} /work/out/diag ${DIAG_LEVEL} 120 ${DIAG_N} \
  > "$OUT/driver.log" 2>&1 &
DRIVER=$!

LAST=0; STALL=0; CAPTURED=0
while kill -0 $DRIVER 2>/dev/null; do
  sleep 15
  N=$(docker exec prodbench-rocketride sh -c 'wc -l < /work/out/diag/per_doc.jsonl 2>/dev/null' | tr -d ' ')
  N=${N:-0}
  if [ "$N" = "$LAST" ]; then STALL=$((STALL+15)); else STALL=0; LAST=$N; fi
  if [ $STALL -ge 90 ] && [ $CAPTURED -eq 0 ]; then
    CAPTURED=1
    log "STALL at $N records — capturing forensics"
    BPID=$(docker exec prodbench-rocketride sh -c "ps ax -o pid,args | grep 'ai/node.py' | grep -v grep | awk '{print \$1}' | head -1")
    log "backend pid=$BPID"
    docker exec prodbench-rocketride sh -c "
      echo '=== ps ==='; ps ax -o pid,rss,nlwp,stat,args | head -25
      echo '=== thread states of backend $BPID ==='
      for t in /proc/$BPID/task/*/stat; do awk '{print \$3}' \$t 2>/dev/null; done | sort | uniq -c
      echo '=== wchan histogram ==='
      for t in /proc/$BPID/task/*/wchan; do cat \$t 2>/dev/null; echo; done | sort | uniq -c | sort -rn | head -12
      echo '=== jspawnhelpers ==='
      ps ax -o pid,ppid,stat,args | grep -i jspawn | grep -v grep
      echo '=== backend fds ==='; ls /proc/$BPID/fd 2>/dev/null | wc -l
      echo '=== mem ==='; grep -E 'VmRSS|Threads' /proc/$BPID/status
    " > "$OUT/wedge_forensics.txt" 2>&1
    log "sending SIGQUIT to backend for JVM thread dump"
    docker exec prodbench-rocketride sh -c "kill -QUIT $BPID" 2>/dev/null
    sleep 5
    docker logs --tail 500 prodbench-rocketride > "$OUT/jvm_dump.txt" 2>&1
    log "forensics captured; killing driver + reaping"
    kill $DRIVER 2>/dev/null
    docker exec prodbench-rocketride sh -c 'pkill -f rr_stepped.py; pkill -9 -f "ai/node.py"' 2>/dev/null
    break
  fi
done
wait $DRIVER 2>/dev/null
docker exec prodbench-rocketride sh -c 'cat /work/out/diag/per_doc.jsonl 2>/dev/null' > "$OUT/per_doc.jsonl"
log "done: records=$(grep -c doc "$OUT/per_doc.jsonl" 2>/dev/null), captured=$CAPTURED"
