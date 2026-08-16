#!/usr/bin/env bash
# THE matched benchmark run: both arms, identical in every respect except the
# framework. This is the only kind of run whose numbers may be compared.
#
# Held identical across arms:
#   same corpus, same N, same document order   (govdocs, sorted[:N])
#   same envelope                              (ARM_CPUS / ARM_MEM, both arms)
#   same rep count                             (REPS)
#   same mode                                  (blast, or c<N> closed-loop)
#   same warm-start policy                     (WARM docs, excluded)
#   arms run ONE AT A TIME                     (no contention)
#
# Only the framework varies. Anything else that differs invalidates the
# comparison, which is why every one of these is recorded in provenance.json.
#
#   bash run/matched_run.sh                 # blast, defaults below
#   MODE=c8 REPS=3 bash run/matched_run.sh  # closed-loop at 8 in flight
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$HERE/results/${STAMP}_${MODE:-blast}"
CORPUS="${CORPUS:-$HOME/bench_corpus}"
N="${N:-200}"; REPS="${REPS:-3}"; WARM="${WARM:-25}"; MODE="${MODE:-blast}"
export ARM_CPUS="${ARM_CPUS:-12.0}" ARM_MEM="${ARM_MEM:-10g}"
export LG_EXTRACTOR="${LG_EXTRACTOR:-tika}"
LG=bench-langgraph; RR=bench-rocketride
mkdir -p "$RUN"
say() { echo "[bench $(date -u +%H:%M:%S)] $*"; }

# ------------------------------------------------------------------- gates
[ "$(uname -m)" = x86_64 ] || { echo "FATAL: need x86_64 (timings are invalid under emulation)"; exit 1; }
[ -d "$CORPUS" ] || { echo "FATAL: no corpus at $CORPUS — run: bash corpus/fetch_govdocs.sh $CORPUS $N"; exit 1; }
have=$(find "$CORPUS" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: $have PDFs < N=$N"; exit 1; }

{ echo "stamp_utc=$STAMP"; echo "arch=$(uname -m)"; echo "nproc=$(nproc)"
  echo "mem_gb=$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"
  echo "os=$(. /etc/os-release && echo "$PRETTY_NAME")"
  echo "kernel=$(uname -r)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "git_dirty=$(git status --porcelain | wc -l | tr -d ' ')"
  echo "corpus=$CORPUS"; echo "n_docs=$N"; echo "reps=$REPS"; echo "mode=$MODE"
  echo "warm_docs=$WARM"; echo "arm_cpus=$ARM_CPUS"; echo "arm_mem=$ARM_MEM"
  echo "lg_extractor=$LG_EXTRACTOR"
  echo "rr_threads=${RR_THREADS:-NONE (engine default)}"
} > "$RUN/environment.txt"
cp "$CORPUS/SHA256SUMS" "$RUN/corpus.sha256" 2>/dev/null || true

say "building both arms"
docker compose build langgraph rocketride 2>&1 | tail -8
for i in langgraph rocketride; do
  docker image inspect "bench-$i:latest" --format "$i {{.Id}} {{.Architecture}}" \
    >> "$RUN/image_ids.txt"
done
say "$(tr '\n' ' ' < "$RUN/image_ids.txt")"

wait_healthy() {  # $1 container, $2 service
  local s
  for _ in $(seq 1 60); do
    s=$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null || echo none)
    [ "$s" = healthy ] && return 0
    if docker logs "$1" 2>&1 | grep -qi "Failed to compile constraints\|Python error 1"; then
      say "BOOT FAILED for $1:"; docker logs --tail 30 "$1"; return 1
    fi
    sleep 5
  done
  docker compose logs --tail 40 "$2"; return 1
}

# ------------------------------------------------------------------ LG arm
say "=== LangGraph — $REPS x $N docs, mode=$MODE ==="
[ "$LG_EXTRACTOR" = tika ] && docker compose up -d tika
docker compose up -d langgraph
wait_healthy "$LG" langgraph || { echo FATAL; exit 1; }
docker exec "$LG" python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8100/meta',timeout=10).read().decode())" \
  > "$RUN/meta_lg.json"
docker run --rm --entrypoint pip bench-langgraph:latest freeze > "$RUN/pip_freeze_lg.txt"

for r in $(seq 1 "$REPS"); do
  D="$RUN/lg/rep$r"; mkdir -p "$D"
  say "lg rep$r/$REPS"
  docker exec -e SAMPLE_MAX_S=7200 -i "$LG" python3 - < bench/cgroup_sampler.py \
    > "$D/sampler.jsonl" 2>"$D/sampler.err" &
  S1=$!
  if [ "$LG_EXTRACTOR" = tika ]; then
    docker exec -e SAMPLE_MAX_S=7200 -i bench-tika python3 - < bench/cgroup_sampler.py \
      > "$D/sampler_tika.jsonl" 2>/dev/null &
    S2=$!
  else S2=""; fi
  sleep 2
  python3 bench/lg_driver.py "$CORPUS" "$D" "$N" "$MODE"
  sleep 2
  kill $S1 ${S2:-} 2>/dev/null || true; wait $S1 ${S2:-} 2>/dev/null || true
  sleep 10   # let the service settle so rep N+1 does not inherit rep N's tail
done
docker compose stop langgraph

# ------------------------------------------------------------------ RR arm
say "=== RocketRide — $REPS x $N docs, mode=$MODE ==="
docker compose up -d rocketride
wait_healthy "$RR" rocketride || { echo FATAL; exit 1; }
docker exec "$RR" sh -c 'sha256sum /opt/rocketride/engine/engine' > "$RUN/engine_sha.txt"
docker logs "$RR" > "$RUN/engine_boot.log" 2>&1
docker exec "$RR" mkdir -p /work/corpus
docker cp bench/rr_driver.py "$RR:/work/rr_driver.py"
docker cp "$CORPUS/." "$RR:/work/corpus/"

for r in $(seq 1 "$REPS"); do
  D="$RUN/rr/rep$r"; mkdir -p "$D"
  say "rr rep$r/$REPS"
  # Clear first: if the driver dies, docker cp would otherwise lift the
  # PREVIOUS rep's records and the report would describe the wrong run.
  docker exec "$RR" rm -rf "/work/out$r"; docker exec "$RR" mkdir -p "/work/out$r"
  docker exec -e SAMPLE_MAX_S=7200 -i "$RR" python3 - < bench/cgroup_sampler.py \
    > "$D/sampler.jsonl" 2>"$D/sampler.err" &
  S3=$!
  sleep 2
  set +e
  docker exec -e RR_THREADS="${RR_THREADS:-}" -e RR_POOL_MAX="${RR_POOL_MAX:-0}" \
    "$RR" python3 /work/rr_driver.py /work/corpus "/work/out$r" "$N" "$MODE" "$WARM"
  DRV=$?
  set -e
  sleep 2
  kill "$S3" 2>/dev/null || true; wait "$S3" 2>/dev/null || true
  [ "$DRV" -eq 0 ] || say "WARNING: rr driver rc=$DRV — rep$r records are suspect"
  docker cp "$RR:/work/out$r/per_doc.jsonl" "$D/per_doc.jsonl" || true
  docker cp "$RR:/work/out$r/manifest.json" "$D/manifest.json" || true
  sleep 10
done
docker logs --tail 300 "$RR" > "$RUN/engine_run.log" 2>&1 || true
docker compose stop rocketride tika 2>/dev/null || true

# --------------------------------------------------------------- provenance
python3 run/write_provenance.py "$RUN"

say "deriving metrics"
set +e
python3 bench/report.py "$RUN" | tee "$RUN/report.txt"
RC=${PIPESTATUS[0]}
set -e

S3_DEST="${BENCH_S3:-s3://rocketride-benchmark-data/leela/bench}/$STAMP/"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  aws s3 cp "$RUN/" "$S3_DEST" --recursive --only-show-errors \
    && say "exfil OK -> $S3_DEST" || say "WARNING: S3 upload failed"
else
  say "WARNING: no aws cli/role (run/install_awscli.sh); results only on box disk"
fi
say "results in $RUN"
[ "$RC" -eq 0 ] && say "RUN PASS" || say "RUN FAIL — see report.txt; numbers kept, never quoted"
exit "$RC"
