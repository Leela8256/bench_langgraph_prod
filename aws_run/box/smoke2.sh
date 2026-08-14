#!/usr/bin/env bash
# Smoke 2 — BOTH arms, 150 documents, native x86, Tika-matched extractors.
#
# Differences from smoke10.sh:
#   - both arms (RocketRide engine 3.3.1 + LangGraph), same corpus, same order
#   - 150 docs, so the warm-start rule applies for the first time (warm_n=25)
#   - EXTRACTOR=tika on the LG side so the arms parse identically
#   - arms run ONE AT A TIME: the compose envelope gives each 12 CPU, and the
#     box is 32 vCPU, so running both at once would have them contend
#   - the RR driver runs INSIDE its container (the engine rejects WebSocket
#     upgrades through Docker's published port -- CONTEXT_SNAPSHOT 4.6)
#
#   bash aws_run/box/smoke2.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$ROOT/aws_run/evidence/smoke2_$STAMP"
CORPUS="${SMOKE_CORPUS:-$HOME/smoke_corpus150}"
N="${SMOKE_N:-150}"
LG=prodbench-langgraph
RR=prodbench-rocketride
export LG_EXTRACTOR="${LG_EXTRACTOR:-tika}"
mkdir -p "$RUN"/{lg/pass1,lg/pass2,rr/pass1,rr/pass2}
say() { echo "[smoke2 $(date -u +%H:%M:%S)] $*"; }

# ---------------------------------------------------------------- 0. gates
arch="$(uname -m)"
[ "$arch" = "x86_64" ] || { echo "FATAL: arch=$arch, need x86_64"; exit 1; }
[ -d "$CORPUS" ] || { echo "FATAL: no corpus at $CORPUS -- run: bash aws_run/box/fetch_smoke_corpus.sh $CORPUS $N"; exit 1; }
have=$(find "$CORPUS" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: $have PDFs < N=$N"; exit 1; }

{
  echo "stamp_utc=$STAMP"; echo "arch=$arch"; echo "nproc=$(nproc)"
  echo "mem_gb=$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "git_dirty=$(git status --porcelain | wc -l | tr -d ' ')"
  echo "corpus=$CORPUS"; echo "n_docs=$N"
  echo "lg_extractor=$LG_EXTRACTOR"; echo "warm_n=25"
} > "$RUN/environment.txt"
cp "$CORPUS/SHA256SUMS" "$RUN/corpus.sha256" 2>/dev/null || true

# ---------------------------------------------------------------- 1. build
say "building both arms (RR engine 3.3.1 -- first build downloads ~230 MB)"
docker compose build langgraph rocketride 2>&1 | tail -15
for img in langgraph rocketride; do
  docker image inspect "prodbench-$img:latest" \
    --format "$img {{.Id}} {{.Architecture}}" >> "$RUN/image_ids.txt"
done
say "$(cat "$RUN/image_ids.txt")"
docker run --rm --entrypoint pip prodbench-langgraph:latest freeze > "$RUN/pip_freeze_lg.txt"

# ------------------------------------------------------------- 2. LG arm
say "=== LangGraph arm (extractor=$LG_EXTRACTOR) ==="
docker compose up -d tika
docker compose up -d langgraph
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$LG" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break; sleep 5
done
[ "$s" = healthy ] || { docker compose logs --tail 60 langgraph; echo "FATAL: LG unhealthy"; exit 1; }
docker exec "$LG" python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8100/meta',timeout=10).read().decode())" \
  > "$RUN/meta_lg.json"

# Sample langgraph AND tika: in tika mode LG's parse work runs in the sidecar,
# so the langgraph cgroup alone understates its true cost.
docker exec -e SAMPLE_MAX_S=5400 -i "$LG" python3 - < aws_run/box/cgroup_sampler.py \
  > "$RUN/lg/pass1/sampler.jsonl" 2>"$RUN/lg/pass1/sampler.err" &
S1=$!
docker exec -e SAMPLE_MAX_S=5400 -i prodbench-tika python3 - < aws_run/box/cgroup_sampler.py \
  > "$RUN/lg/pass1/sampler_tika.jsonl" 2>/dev/null &
S2=$!
sleep 2
python3 aws_run/box/lg_smoke_driver.py "$CORPUS" "$RUN/lg/pass1" "$N"
sleep 2
kill "$S1" "$S2" 2>/dev/null || true; wait "$S1" "$S2" 2>/dev/null || true
say "lg pass2 (determinism)"
python3 aws_run/box/lg_smoke_driver.py "$CORPUS" "$RUN/lg/pass2" "$N"
docker compose stop langgraph

# ------------------------------------------------------------- 3. RR arm
say "=== RocketRide arm (engine 3.3.1, driver runs container-side) ==="
docker compose up -d rocketride
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$RR" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break; sleep 5
done
[ "$s" = healthy ] || { docker compose logs --tail 60 rocketride; echo "FATAL: RR unhealthy"; exit 1; }

# No volume mount on the RR service, and no scp into a container -- docker cp
# is how the driver and corpus get in, and results come back out.
docker exec "$RR" mkdir -p /work/corpus /work/out1 /work/out2
docker cp aws_run/box/rr_smoke_driver.py "$RR:/work/rr_smoke_driver.py"
say "copying $N PDFs into the RR container"
for f in $(find "$CORPUS" -name '*.pdf' -printf '%f\n' | sort | head -n "$N"); do
  docker cp "$CORPUS/$f" "$RR:/work/corpus/$f"
done

docker exec -e SAMPLE_MAX_S=5400 -i "$RR" python3 - < aws_run/box/cgroup_sampler.py \
  > "$RUN/rr/pass1/sampler.jsonl" 2>"$RUN/rr/pass1/sampler.err" &
S3=$!
sleep 2
docker exec "$RR" python3 /work/rr_smoke_driver.py /work/corpus /work/out1 "$N"
sleep 2
kill "$S3" 2>/dev/null || true; wait "$S3" 2>/dev/null || true
docker cp "$RR:/work/out1/per_doc.jsonl"  "$RUN/rr/pass1/per_doc.jsonl"
docker cp "$RR:/work/out1/manifest.json"  "$RUN/rr/pass1/manifest.json"

say "rr pass2 (determinism)"
docker exec "$RR" python3 /work/rr_smoke_driver.py /work/corpus /work/out2 "$N"
docker cp "$RR:/work/out2/per_doc.jsonl"  "$RUN/rr/pass2/per_doc.jsonl"
docker cp "$RR:/work/out2/manifest.json"  "$RUN/rr/pass2/manifest.json"
docker compose stop rocketride tika

# ---------------------------------------------------------------- 4. report
say "deriving metrics via metrics/ (warm_n=25) + cross-arm parity"
set +e
python3 aws_run/box/smoke2_report.py "$RUN" | tee "$RUN/report.txt"
RC=${PIPESTATUS[0]}
set -e

# ----------------------------------------------------------------- 5. exfil
S3_DEST="${BENCH_S3:-s3://rocketride-benchmark-data/leela/smoke2}/$STAMP/"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  aws s3 cp "$RUN/" "$S3_DEST" --recursive --only-show-errors \
    && say "exfil OK -> $S3_DEST" \
    || say "WARNING: S3 upload failed; results only on box disk"
else
  say "WARNING: no aws cli/role — run aws_run/box/install_awscli.sh; results only on box disk"
fi

say "evidence in $RUN"
[ "$RC" -eq 0 ] && say "SMOKE2 PASS — both arms M0 green" \
                || say "SMOKE2 FAIL — see report.txt; numbers kept, never quoted"
exit "$RC"
