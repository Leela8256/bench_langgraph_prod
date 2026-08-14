#!/usr/bin/env bash
# BLAST run — both arms, 150 docs, 3 reps each, native x86, Tika-matched.
#
# Blast = the whole backlog is submitted at once and the framework schedules
# it. Latency here is BATCH-POSITION latency (includes queue wait) and must
# never be compared with closed-loop service latency.
#
# 3 reps because one run is an anecdote; stability is reported as CV with a
# threshold frozen before results were seen (metrics/stability.py).
#
# Arms run ONE AT A TIME: each is capped at 12 CPU by the compose envelope,
# so concurrent arms would contend and neither number would mean anything.
#
#   bash aws_run/box/blast_run.sh
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$ROOT/aws_run/evidence/blast_$STAMP"
CORPUS="${SMOKE_CORPUS:-$HOME/smoke_corpus150}"
N="${SMOKE_N:-150}"; REPS="${REPS:-3}"
LG=prodbench-langgraph; RR=prodbench-rocketride
export LG_EXTRACTOR="${LG_EXTRACTOR:-tika}"
mkdir -p "$RUN"
say() { echo "[blast $(date -u +%H:%M:%S)] $*"; }

arch="$(uname -m)"
[ "$arch" = x86_64 ] || { echo "FATAL: arch=$arch, need x86_64"; exit 1; }
[ -d "$CORPUS" ] || { echo "FATAL: no corpus at $CORPUS — run: bash aws_run/box/fetch_smoke_corpus.sh $CORPUS $N"; exit 1; }
have=$(find "$CORPUS" -name '*.pdf' | wc -l | tr -d ' ')
[ "$have" -ge "$N" ] || { echo "FATAL: $have PDFs < N=$N"; exit 1; }

{ echo "stamp_utc=$STAMP"; echo "arch=$arch"; echo "nproc=$(nproc)"
  echo "mem_gb=$(awk '/MemTotal/{printf "%.0f",$2/1048576}' /proc/meminfo)"
  echo "docker=$(docker version --format '{{.Server.Version}}')"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "git_dirty=$(git status --porcelain | wc -l | tr -d ' ')"
  echo "corpus=$CORPUS"; echo "n_docs=$N"; echo "reps=$REPS"
  echo "mode=blast"; echo "lg_extractor=$LG_EXTRACTOR"
} > "$RUN/environment.txt"
cp "$CORPUS/SHA256SUMS" "$RUN/corpus.sha256" 2>/dev/null || true
CORPUS_SHA=$(sha256sum "$CORPUS/SHA256SUMS" 2>/dev/null | cut -d' ' -f1)

say "building both arms (RR engine 3.3.1)"
docker compose build langgraph rocketride 2>&1 | tail -8
for i in langgraph rocketride; do
  docker image inspect "prodbench-$i:latest" --format "$i {{.Id}} {{.Architecture}}" \
    >> "$RUN/image_ids.txt"
done
LG_DIGEST=$(docker image inspect prodbench-langgraph:latest --format '{{.Id}}')
RR_DIGEST=$(docker image inspect prodbench-rocketride:latest --format '{{.Id}}')

# ------------------------------------------------------------------ LG arm
say "=== LangGraph — $REPS blast reps of $N docs ==="
docker compose up -d tika langgraph
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$LG" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break; sleep 5
done
[ "$s" = healthy ] || { docker compose logs --tail 40 langgraph; echo FATAL; exit 1; }
docker exec "$LG" python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8100/meta',timeout=10).read().decode())" \
  > "$RUN/meta_lg.json"

for r in $(seq 1 "$REPS"); do
  D="$RUN/lg/rep$r"; mkdir -p "$D"
  say "lg rep$r/$REPS"
  docker exec -e SAMPLE_MAX_S=3600 -i "$LG" python3 - < aws_run/box/cgroup_sampler.py \
    > "$D/sampler.jsonl" 2>"$D/sampler.err" &
  S1=$!
  docker exec -e SAMPLE_MAX_S=3600 -i prodbench-tika python3 - < aws_run/box/cgroup_sampler.py \
    > "$D/sampler_tika.jsonl" 2>/dev/null &
  S2=$!
  sleep 2
  python3 aws_run/box/lg_smoke_driver.py "$CORPUS" "$D" "$N" blast
  sleep 2
  kill "$S1" "$S2" 2>/dev/null || true; wait "$S1" "$S2" 2>/dev/null || true
  # Let the service settle so the next rep does not inherit this one's tail.
  sleep 10
done
docker compose stop langgraph

# ------------------------------------------------------------------ RR arm
say "=== RocketRide — $REPS blast reps of $N docs ==="
docker compose up -d rocketride
for i in $(seq 1 60); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$RR" 2>/dev/null || echo none)
  [ "$s" = healthy ] && break; sleep 5
done
[ "$s" = healthy ] || { docker compose logs --tail 40 rocketride; echo FATAL; exit 1; }

docker exec "$RR" mkdir -p /work/corpus
docker cp aws_run/box/rr_smoke_driver.py "$RR:/work/rr_smoke_driver.py"
say "copying $N PDFs into the RR container"
docker cp "$CORPUS/." "$RR:/work/corpus/"

for r in $(seq 1 "$REPS"); do
  D="$RUN/rr/rep$r"; mkdir -p "$D"
  say "rr rep$r/$REPS"
  docker exec "$RR" rm -rf "/work/out$r"; docker exec "$RR" mkdir -p "/work/out$r"
  docker exec -e SAMPLE_MAX_S=3600 -i "$RR" python3 - < aws_run/box/cgroup_sampler.py \
    > "$D/sampler.jsonl" 2>"$D/sampler.err" &
  S3=$!
  sleep 2
  docker exec "$RR" python3 /work/rr_smoke_driver.py /work/corpus "/work/out$r" "$N" blast
  sleep 2
  kill "$S3" 2>/dev/null || true; wait "$S3" 2>/dev/null || true
  docker cp "$RR:/work/out$r/per_doc.jsonl" "$D/per_doc.jsonl"
  docker cp "$RR:/work/out$r/manifest.json" "$D/manifest.json"
  sleep 10
done
docker compose stop rocketride tika

# -------------------------------------------------------------- provenance
python3 - "$RUN" "$CORPUS_SHA" "$LG_DIGEST" "$RR_DIGEST" "$N" "$REPS" <<'PY'
import json, sys, pathlib
run, corpus_sha, lg_d, rr_d, n, reps = sys.argv[1:7]
run = pathlib.Path(run)
env = dict(l.split("=",1) for l in (run/"environment.txt").read_text().splitlines() if "=" in l)
meta = json.loads((run/"meta_lg.json").read_text()) if (run/"meta_lg.json").exists() else {}
wv = meta.get("workload_versions", {})
rec = {
  "run_id": run.name, "timestamp_utc": env.get("stamp_utc"),
  "git_commit": env.get("git_sha"),
  "image_digest": {"langgraph": lg_d, "rocketride": rr_d},
  "framework_version": {"langgraph": wv.get("extractor"), "rocketride_engine": "3.3.1"},
  "instance_type": "c7i.8xlarge", "architecture": env.get("arch"),
  "cpu_count": int(env.get("nproc", 0)), "ram_gb": int(env.get("mem_gb", 0)),
  "corpus_manifest_sha256": corpus_sha, "corpus_n_docs": int(n),
  "parser": env.get("lg_extractor"),
  "parser_config_hash": wv.get("extractor"),
  "chunk_config": wv.get("split"),
  "embedding_model": (wv.get("embedding") or {}).get("model_id"),
  "offered_concurrency": int(n),
  "configured_concurrency": "LG default executor min(32,cpu_count+4); RR engine-internal",
  "warmup_policy": "uncounted warm-up doc per arm; blast window is the whole batch",
  "timeout_s": 300, "mode": "blast", "reps": int(reps),
}
(run/"provenance.json").write_text(json.dumps(rec, indent=1))
print("provenance written")
PY

say "deriving metrics + stability"
set +e
python3 aws_run/box/blast_report.py "$RUN" | tee "$RUN/report.txt"
RC=${PIPESTATUS[0]}
set -e

S3_DEST="${BENCH_S3:-s3://rocketride-benchmark-data/leela/blast}/$STAMP/"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  aws s3 cp "$RUN/" "$S3_DEST" --recursive --only-show-errors \
    && say "exfil OK -> $S3_DEST" || say "WARNING: S3 upload failed"
else
  say "WARNING: no aws cli/role; results only on box disk"
fi
say "evidence in $RUN"
[ "$RC" -eq 0 ] && say "BLAST PASS" || say "BLAST FAIL — see report.txt"
exit "$RC"
