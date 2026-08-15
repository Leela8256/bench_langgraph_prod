#!/usr/bin/env bash
# RocketRide arm only — blast, 200 docs, 25 warm-start docs, 1 rep, NO caps.
#
# ⚠️  Uncapped by request. The result is NOT comparable with the capped
# LangGraph blast run (12 CPU / 10 GB) or with any future capped run. It
# answers "what does RocketRide do with the whole box", not "which framework
# is faster in an equal envelope". The report records the envelope so this
# cannot be mistaken later.
#
# 1 rep by request: no CV, so nothing here is quotable as a stable number.
#
#   bash aws_run/box/rr_run.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$ROOT/aws_run/evidence/rr_$STAMP"
CORPUS="${SMOKE_CORPUS:-$HOME/smoke_corpus200}"
N="${SMOKE_N:-200}"
WARM="${WARM_DOCS:-25}"
RR=prodbench-rocketride
COMPOSE="-f docker-compose.yml -f docker-compose.norestrict.yml"
mkdir -p "$RUN/rr/rep1"
say() { echo "[rr-run $(date -u +%H:%M:%S)] $*"; }

[ "$(uname -m)" = x86_64 ] || { echo "FATAL: need x86_64"; exit 1; }
[ -d "$CORPUS" ] || { echo "FATAL: no corpus at $CORPUS"; exit 1; }
have=$(find "$CORPUS" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: $have PDFs < N=$N"; exit 1; }

{ echo "stamp_utc=$STAMP"; echo "arch=$(uname -m)"; echo "nproc=$(nproc)"
  echo "mem_gb=$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "corpus=$CORPUS"; echo "n_docs=$N"; echo "reps=1"; echo "mode=blast"
  echo "warm_docs=$WARM"; echo "threads_requested=${RR_THREADS:-NONE (engine default)}"
  echo "cpu_cap=NONE (norestrict overlay)"; echo "arm=rocketride-only"
} > "$RUN/environment.txt"
cp "$CORPUS/SHA256SUMS" "$RUN/corpus.sha256" 2>/dev/null || true

say "building rocketride (engine 3.3.1 + onnxruntime-gpu pin workaround)"
docker compose $COMPOSE build rocketride 2>&1 | tail -12
docker image inspect prodbench-rocketride:latest \
  --format 'rocketride {{.Id}} {{.Architecture}}' > "$RUN/image_ids.txt"

say "starting engine UNCAPPED"
docker compose $COMPOSE up -d rocketride
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$RR" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break
  # Boot failures are silent in healthcheck terms -- surface them early.
  if docker logs "$RR" 2>&1 | grep -qi "Failed to compile constraints\|Python error"; then
    say "ENGINE BOOT FAILED:"; docker logs --tail 30 "$RR"; exit 1
  fi
  sleep 5
done
[ "$s" = healthy ] || { docker logs --tail 40 "$RR"; echo "FATAL: unhealthy"; exit 1; }
say "engine healthy after ~$((i * 5))s"
docker logs "$RR" > "$RUN/engine_boot.log" 2>&1

# Prove which engine actually booted, rather than trusting the build arg.
docker exec "$RR" sh -c 'sha256sum /opt/rocketride/engine/engine' > "$RUN/engine_sha.txt"
say "engine binary: $(cat "$RUN/engine_sha.txt")"

# Does host -> container WebSocket work now that the engine binds 0.0.0.0?
# CONTEXT_SNAPSHOT 4.6 recorded this failing and filed it as a product defect;
# the engine was very likely just never told to bind an external interface.
# Recording the answer either retracts that finding or confirms it properly.
say "testing host->container reachability on :5565"
python3 - > "$RUN/host_ws_probe.txt" 2>&1 <<'PY' || true
import socket, json
r = {}
s = socket.socket(); s.settimeout(5)
r["tcp_connect_host_to_published_port"] = (s.connect_ex(("127.0.0.1", 5565)) == 0)
s.close()
print(json.dumps(r))
PY
say "host reachability: $(cat "$RUN/host_ws_probe.txt")"

# Clear out1 FIRST: if the driver dies, docker cp would otherwise copy a
# previous run's per_doc.jsonl and the report would silently describe
# the wrong run (this happened -- stale file, wrong client_pool).
docker exec "$RR" rm -rf /work/out1
docker exec "$RR" mkdir -p /work/corpus /work/out1
docker cp aws_run/box/rr_smoke_driver.py "$RR:/work/rr_smoke_driver.py"
say "copying $N PDFs into the container"
docker cp "$CORPUS/." "$RR:/work/corpus/"

say "blast: $N docs, 1 rep, warm-start $WARM docs (excluded)"
docker exec -e SAMPLE_MAX_S=5400 -i "$RR" python3 - < aws_run/box/cgroup_sampler.py \
  > "$RUN/rr/rep1/sampler.jsonl" 2>"$RUN/rr/rep1/sampler.err" &
S=$!
sleep 2
set +e
docker exec -e RR_POOL_MAX="${RR_POOL_MAX:-0}" -e RR_THREADS="${RR_THREADS:-}" "$RR" python3 /work/rr_smoke_driver.py /work/corpus /work/out1 "$N" blast "$WARM"
DRV=$?
set -e
sleep 2
kill "$S" 2>/dev/null || true; wait "$S" 2>/dev/null || true
say "driver rc=$DRV"
[ "$DRV" -eq 0 ] || say "WARNING: driver FAILED (rc=$DRV) — any records below are suspect"

docker cp "$RR:/work/out1/per_doc.jsonl" "$RUN/rr/rep1/per_doc.jsonl" || true
docker cp "$RR:/work/out1/manifest.json" "$RUN/rr/rep1/manifest.json" || true
docker logs --tail 200 "$RR" > "$RUN/engine_run.log" 2>&1 || true
docker compose $COMPOSE stop rocketride

say "deriving metrics"
set +e
python3 aws_run/box/blast_report.py "$RUN" | tee "$RUN/report.txt"
RC=${PIPESTATUS[0]}
set -e

S3_DEST="${BENCH_S3:-s3://rocketride-benchmark-data/leela/rr}/$STAMP/"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  aws s3 cp "$RUN/" "$S3_DEST" --recursive --only-show-errors \
    && say "exfil OK -> $S3_DEST" || say "WARNING: upload failed"
fi
say "evidence in $RUN"
exit "$RC"
