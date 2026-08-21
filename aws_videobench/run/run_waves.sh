#!/usr/bin/env bash
# Wave campaign runner, ON THE BOX: TOTAL videos in waves of W, each wave
# sharded across K parallel engine instances (LPT bin-packing by file size),
# torn down between waves so engine scratch never exceeds one wave.
#
# Solves both measured blockers on current hardware:
#   storage — scratch is released every wave (engines hold uploads for the
#             container lifetime); wave corpus is deleted after its wave;
#             peak disk = fixed footprint + W x ~141 MB x 2
#   cores   — measured as shipped: ONE engine per arm (its ~6-core ceiling
#             is a reported finding, not something the harness works around)
#
# Corpus modes:
#   CORPUS_MODE=replicate  (default) cycle the ami30h corpus into TOTAL
#                          hardlinked docs with distinct names/doc_ids —
#                          zero extra disk, zero download. HONEST AS A
#                          SCALE/STRESS RUN ONLY: content repeats every 62.
#   CORPUS_MODE=s3         each wave pulls run-ready .avi from $S3_CORPUS
#                          (staged once beforehand), deleted after the wave.
#
# Resumable: finished waves are recorded in waves_done (synced to S3);
# re-running the same CAMP skips them.
#
#   TOTAL=500 W=100 K=5 nohup bash run/run_waves.sh > ~/logs/waves.log 2>&1 &
set -euo pipefail
cd "$(dirname "$0")/.."

TOTAL="${TOTAL:-500}"
W="${W:-100}"
K="${K:-1}"   # ONE engine — the benchmark measures the engine as it ships
CORPUS_MODE="${CORPUS_MODE:-replicate}"
SRC_DIR="${SRC_DIR:-$HOME/bench_corpus_ami30h}"
S3_CORPUS="${S3_CORPUS:-}"
CAMP="${CAMP:-waves-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="results/$CAMP"
S3_DEST="s3://rocketride-benchmark-data/leela/videobench/$CAMP/"
WAVE_DIR="$HOME/wave_corpus"
export BENCH_PIPE="${BENCH_PIPE:-/pipe/benchmark_video_detect.pipe}"
export BENCH_TIMEOUT_S="${BENCH_TIMEOUT_S:-21600}"
AWS_BIN="$(command -v aws || echo /usr/local/bin/aws)"
[ -x "$AWS_BIN" ] || AWS_BIN="$HOME/.local/bin/aws"
mkdir -p "$OUT"

echo "== preflight"
# S3 GET is load-bearing for s3 mode and for resume; verify the grant NOW.
if ! "$AWS_BIN" s3 cp "s3://rocketride-benchmark-data/leela/videobench/rundetect-20260820T012432Z/manifest.json" /tmp/s3probe.json --quiet; then
  echo "FATAL: instance role cannot GET from the bucket — ask admin for" >&2
  echo "s3:GetObject + s3:ListBucket on rocketride-benchmark-data/leela/*" >&2
  exit 1
fi
echo "   s3 GET ok"
docker container prune -f >/dev/null
rm -rf "$HOME/ami_cache"
FREE_GB=$(df -Pm / | awk 'NR==2{print int($4/1024)}')
NEED_GB=$(( W * 141 * 2 / 1024 + 6 ))
[ "$FREE_GB" -ge "$NEED_GB" ] || { echo "FATAL: ${FREE_GB}G free, wave needs ~${NEED_GB}G" >&2; exit 1; }
echo "   disk ok: ${FREE_GB}G free, ~${NEED_GB}G per wave"
[ "$CORPUS_MODE" = "replicate" ] && [ ! -d "$SRC_DIR" ] && { echo "FATAL: $SRC_DIR missing" >&2; exit 1; }

echo "== engines: generating compose for K=$K"
python3 run/gen_engines.py "$K" > docker-compose.engines.yml
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.engines.yml"
$COMPOSE build rocketride smoke   # ensures image tags exist; engines reuse them

STATE="$OUT/waves_done"
touch "$STATE"
N_WAVES=$(( (TOTAL + W - 1) / W ))
echo "== campaign $CAMP: $TOTAL docs, $N_WAVES waves of <=$W, K=$K engines, mode=$CORPUS_MODE"

FIRST_BOOT=1
for wave in $(seq 1 "$N_WAVES"); do
  if grep -qx "wave$wave" "$STATE"; then echo "== wave $wave already done, skip"; continue; fi
  n_this=$(( TOTAL - (wave - 1) * W )); [ "$n_this" -gt "$W" ] && n_this=$W
  echo "== wave $wave/$N_WAVES ($n_this docs) — corpus"
  rm -rf "$WAVE_DIR"; mkdir -p "$WAVE_DIR"

  if [ "$CORPUS_MODE" = "replicate" ]; then
    python3 - "$SRC_DIR" "$WAVE_DIR" "$wave" "$n_this" "$W" <<'PY'
import json, os, pathlib, sys
src, dst, wave, n, w = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
vids = sorted(src.glob("*.avi"))
durs = json.loads((src / "corpus_manifest.json").read_text()).get("duration_s", {})
out_durs = {}
for i in range(n):
    g = (wave - 1) * w + i                    # global doc index -> stable identity
    orig = vids[g % len(vids)]
    name = f"w{wave:03d}_{i:04d}_{orig.name}"
    os.link(orig, dst / name)                 # hardlink: zero bytes
    out_durs[name] = durs.get(orig.name, 0)
(dst / "corpus_manifest.json").write_text(json.dumps({
    "corpus": "ami30h-replicated", "wave": wave, "duration_s": out_durs,
    "note": "hardlinked replicas of bench_corpus_ami30h — scale/stress corpus, content repeats every 62 docs"}))
print(f"  {n} hardlinked docs (0 extra bytes)")
PY
  else
    [ -n "$S3_CORPUS" ] || { echo "FATAL: CORPUS_MODE=s3 needs S3_CORPUS" >&2; exit 1; }
    "$AWS_BIN" s3 cp "$S3_CORPUS/wave$wave/" "$WAVE_DIR/" --recursive --quiet
    echo "  $(find "$WAVE_DIR" -name '*.avi' | wc -l | tr -d ' ') docs pulled from S3"
  fi

  # LPT shard by size: biggest file to the emptiest bin — balanced engines.
  python3 - "$WAVE_DIR" "$K" <<'PY'
import json, os, pathlib, shutil, sys
d, k = pathlib.Path(sys.argv[1]), int(sys.argv[2])
vids = sorted(d.glob("*.avi"), key=lambda p: -p.stat().st_size)
bins = [[0, i, []] for i in range(1, k + 1)]
for v in vids:
    bins.sort()
    bins[0][0] += v.stat().st_size
    bins[0][2].append(v)
for size, i, items in bins:
    sd = d / f"shard_{i}"; sd.mkdir()
    for v in items: os.link(v, sd / v.name)
    shutil.copy(d / "corpus_manifest.json", sd / "corpus_manifest.json")
    print(f"  shard_{i}: {len(items)} docs, {size/1e9:.1f} GB")
PY

  echo "== wave $wave — engines up"
  if [ "$FIRST_BOOT" = "1" ]; then
    $COMPOSE up -d rr1          # alone first: cold-cache pip installs must not race
    for i in $(seq 1 60); do
      [ "$(docker inspect -f '{{.State.Health.Status}}' videobench-rr1 2>/dev/null)" = "healthy" ] && break
      [ "$i" = 60 ] && { echo "FATAL: rr1 never healthy"; exit 1; }
      sleep 5
    done
    FIRST_BOOT=0
  fi
  for k in $(seq 1 "$K"); do $COMPOSE up -d "rr$k"; done
  for k in $(seq 1 "$K"); do
    for i in $(seq 1 60); do
      [ "$(docker inspect -f '{{.State.Health.Status}}' "videobench-rr$k" 2>/dev/null)" = "healthy" ] && break
      [ "$i" = 60 ] && { echo "FATAL: rr$k never healthy"; exit 1; }
      sleep 5
    done
  done
  echo "   $K engines healthy"

  ( echo "ts,container,cpu_usage_usec,mem_current"
    while docker inspect -f '{{.State.Running}}' videobench-rr1 2>/dev/null | grep -q true; do
      for k in $(seq 1 "$K"); do
        line=$(docker exec "videobench-rr$k" sh -c \
          'awk "/^usage_usec/{print \$2}" /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.current' \
          2>/dev/null | tr '\n' ',') || line=""
        [ -n "$line" ] && echo "$(date +%s),rr$k,${line%,}"
      done
      sleep 15
    done ) > "$OUT/wave${wave}_cgroup.csv" &
  SAMPLER_PID=$!
  ( while true; do "$AWS_BIN" s3 sync "$OUT" "$S3_DEST" --quiet 2>/dev/null || true; sleep 60; done ) &
  SYNC_PID=$!
  trap 'kill $SAMPLER_PID $SYNC_PID 2>/dev/null || true' EXIT

  echo "== wave $wave — $K shard drivers"
  pids=(); rc_all=0
  for k in $(seq 1 "$K"); do
    cnt=$(find "$WAVE_DIR/shard_$k" -name '*.avi' | wc -l | tr -d ' ')
    [ "$cnt" -gt 0 ] || continue
    CORPUS="$WAVE_DIR" $COMPOSE run --rm \
      -e ROCKETRIDE_URI="ws://rr$k:5565/task/service" \
      smoke python /bench/bench_video.py "/corpus/shard_$k" \
      "/results/$CAMP/wave${wave}_shard$k" "$cnt" blast 0 \
      > "$OUT/wave${wave}_shard$k.log" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || rc_all=1; done

  kill $SAMPLER_PID $SYNC_PID 2>/dev/null || true
  echo "== wave $wave — teardown (drivers rc=$rc_all)"
  $COMPOSE down
  rm -rf "$WAVE_DIR"
  if [ "$rc_all" = "0" ]; then
    echo "wave$wave" >> "$STATE"
  else
    echo "WAVE $wave FAILED — not marked done; campaign continues, rerun resumes it"
  fi
  "$AWS_BIN" s3 sync "$OUT" "$S3_DEST" --quiet || true
  df -h / | tail -1
done

done_n=$(wc -l < "$STATE" | tr -d ' ')
"$AWS_BIN" s3 sync "$OUT" "$S3_DEST" --quiet || true
echo "campaign $CAMP finished: $done_n/$N_WAVES waves complete, results: $S3_DEST"
[ "$done_n" -eq "$N_WAVES" ]
