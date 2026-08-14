#!/usr/bin/env bash
# LangGraph smoke test on the AWS box: build native amd64 -> boot -> 10 docs
# x2 passes -> M0/M1/M2/M7 from metrics/.
#
# Pass 1 is sampled and produces M1/M2/M7. Pass 2 is unsampled and exists
# only to give M0 determinism a second observation to compare against; a
# single pass leaves the correctness gate incomplete by design (fail-closed).
#
#   bash aws_run/box/smoke10.sh            # from the repo root
#   LG_EXTRACTOR=tika bash aws_run/box/smoke10.sh   # tika sidecar instead
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$ROOT/aws_run/evidence/smoke_$STAMP"
RUN1="$RUN/pass1"; RUN2="$RUN/pass2"
CORPUS="${SMOKE_CORPUS:-$HOME/smoke_corpus}"
N="${SMOKE_N:-10}"
SVC=prodbench-langgraph
mkdir -p "$RUN1" "$RUN2"
say() { echo "[smoke $(date -u +%H:%M:%S)] $*"; }

# ---------------------------------------------------------------- 0. gates
say "preconditions"
arch="$(uname -m)"
[ "$arch" = "x86_64" ] || { echo "FATAL: arch=$arch, need x86_64 (checklist 1.4)"; exit 1; }
docker version >/dev/null || { echo "FATAL: docker unusable"; exit 1; }
[ -d "$CORPUS" ] || { echo "FATAL: no corpus at $CORPUS — run aws_run/box/fetch_smoke_corpus.sh"; exit 1; }
have=$(find "$CORPUS" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: $have PDFs < N=$N"; exit 1; }

{
  echo "stamp_utc=$STAMP"
  echo "arch=$arch"
  echo "nproc=$(nproc)"
  echo "mem_gb=$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "compose=$(docker compose version --short)"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "git_dirty=$(git status --porcelain | wc -l | tr -d ' ')"
  echo "corpus=$CORPUS"
  echo "n_docs=$N"
  echo "lg_extractor=${LG_EXTRACTOR:-pypdf}"
} > "$RUN/environment.txt"
cp "$CORPUS/SHA256SUMS" "$RUN/corpus.sha256" 2>/dev/null || true

# ---------------------------------------------------------------- 1. build
say "building langgraph image (native amd64, first ever non-emulated build)"
docker compose build langgraph 2>&1 | tail -20
docker image inspect prodbench-langgraph:latest \
  --format '{{.Id}} {{.Architecture}} {{.Os}}' > "$RUN/image_id.txt"
say "image: $(cat "$RUN/image_id.txt")"

# The Dockerfile pins only pypdf; everything else is >=. Capture what the
# resolver actually chose HERE so the pinning decision has real data.
docker run --rm --entrypoint pip prodbench-langgraph:latest freeze \
  > "$RUN/pip_freeze.txt"
say "resolved deps -> pip_freeze.txt ($(wc -l < "$RUN/pip_freeze.txt") pkgs)"
grep -E '^(langchain-text-splitters|sentence-transformers|langgraph|pypdf|torch|transformers)=' \
  "$RUN/pip_freeze.txt" || true

# ----------------------------------------------------------------- 2. boot
say "starting langgraph (+tika if EXTRACTOR=tika)"
if [ "${LG_EXTRACTOR:-pypdf}" = "tika" ]; then
  docker compose up -d tika
fi
docker compose up -d langgraph

say "waiting for /health/ready (warm-up gated)"
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$SVC" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break
  sleep 5
done
[ "$s" = healthy ] || { docker compose logs --tail 60 langgraph; echo "FATAL: not healthy"; exit 1; }
say "healthy after ~$((i * 5))s"

docker exec "$SVC" python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8100/meta',timeout=10).read().decode())" \
  > "$RUN/meta.json"
say "captured /meta"
python3 -c "
import json;m=json.load(open('$RUN/meta.json'));w=m.get('workload_versions',{})
print('  arch=%s executor_workers(reported)=%s' % (m.get('architecture'), m.get('executor_workers')))
print('  extractor=%s' % (w.get('extractor'),))
print('  embedding=%s' % (w.get('embedding'),))
print('  split=%s' % (w.get('split'),))"

# ------------------------------------------------------- 3. pass 1 (sampled)
say "pass 1: $N docs, sequential closed-loop, sampled"
# SAMPLE_MAX_S is a self-destruct: killing the local `docker exec` client does
# not reliably reap the python inside the container, and a sampler left
# running would pollute the next run's CPU accounting.
docker exec -e SAMPLE_MAX_S=900 -e SAMPLE_INTERVAL_S=0.5 -i "$SVC" \
  python3 - < aws_run/box/cgroup_sampler.py \
  > "$RUN1/sampler.jsonl" 2>"$RUN1/sampler.err" &
SAMPLER=$!
sleep 2
python3 aws_run/box/lg_smoke_driver.py "$CORPUS" "$RUN1" "$N"
sleep 2
kill "$SAMPLER" 2>/dev/null || true
wait "$SAMPLER" 2>/dev/null || true
docker exec "$SVC" pkill -f '^python3 -$' 2>/dev/null || true
samples=$(wc -l < "$RUN1/sampler.jsonl" | tr -d ' ')
say "sampler: $samples samples ($(head -c 120 "$RUN1/sampler.err" 2>/dev/null || true))"
[ "$samples" -ge 3 ] || say "WARNING: <3 samples — M7 will be null (see sampler.err)"

# ----------------------------------------------------- 4. pass 2 (unsampled)
say "pass 2: same $N docs again — feeds the M0 determinism check"
python3 aws_run/box/lg_smoke_driver.py "$CORPUS" "$RUN2" "$N"

# ---------------------------------------------------------------- 5. report
say "deriving M0/M1/M2/M7 via metrics/"
set +e
python3 aws_run/box/smoke_report.py "$RUN1" "$RUN2" | tee "$RUN/report.txt"
RC=${PIPESTATUS[0]}
set -e

# ------------------------------------------------------------------ 6. exfil
# S3 is the ONLY way results leave the box (no scp, no port forwarding), and
# the box auto-stops after 1 h idle. Uploading RAW records + sampler streams,
# not just the report: every metric is re-derivable from those forever, and a
# report alone cannot be recomputed or re-gated.
S3_DEST="${BENCH_S3:-s3://rocketride-benchmark-data/leela/smoke}/$STAMP/"
if aws sts get-caller-identity >/dev/null 2>&1; then
  if aws s3 cp "$RUN/" "$S3_DEST" --recursive --only-show-errors; then
    say "exfil OK -> $S3_DEST"
    aws s3 ls "$S3_DEST" --recursive | tee "$RUN/s3_listing.txt"
  else
    say "WARNING: S3 upload failed — results exist ONLY on this box's disk"
  fi
else
  say "WARNING: no instance role — skipping S3. Results are ONLY on box disk at $RUN"
fi

say "evidence in $RUN"
ls -la "$RUN" "$RUN1"
echo
if [ "$RC" -eq 0 ]; then
  say "SMOKE PASS — M0 gate green"
else
  say "SMOKE FAIL — M0 gate red (see report.txt); numbers kept, never quoted"
fi
exit "$RC"
