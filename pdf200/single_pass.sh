#!/bin/bash
# User-curtailed single pass: wait for gt-rr (doubles as RR L1) -> build RR
# ground truth -> probe gate -> RR L4/16/64 -> LG L1/4/16/64 -> drift-post.
# No reps, no calibration (provisional 300s timeouts, recorded).
set -u
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
cd "$ROOT"
R="runs/pdf200"
N=200
T=300
say() { echo "[single $(date +%H:%M:%S)] $*"; }

# 0. wait for the capture pass to finish
until docker exec prodbench-rocketride sh -c "grep -q level_meta /work/out/gt-rr/per_doc.jsonl" 2>/dev/null; do
  sleep 30
done
say "gt-rr complete"
docker cp prodbench-rocketride:/work/out/gt-rr "$R/gt-rr" >/dev/null 2>&1

# 1. RR ground truth from capture; capture also becomes rep1-rr/L1
/usr/bin/python3 - <<'PY'
import json, shutil
from pathlib import Path
rows=[json.loads(l) for l in open("runs/pdf200/gt-rr/per_doc.jsonl")]
docs=[r for r in rows if r.get("kind")!="level_meta"]
ok=[r for r in docs if r.get("ok")]
with open("results200/ground_truth_rr.jsonl","w") as out:
    for r in ok:
        out.write(json.dumps({"doc":r["doc"],"chunk_sha256":r["chunk_sha256"],
                              "n_chunks":r["n_chunks"],"total_chars":r["total_chars"]})+"\n")
print(f"RR ground truth: {len(ok)}/{len(docs)} captured")
dst=Path("runs/pdf200/rep1-rr/L1"); dst.mkdir(parents=True, exist_ok=True)
shutil.copy("runs/pdf200/gt-rr/per_doc.jsonl", dst/"per_doc.jsonl")
note=dst/"NOTE.txt"
note.write_text("This L1 data IS the ground-truth capture pass (closed-loop level 1, "
                "same protocol). Its chunk hashes therefore match ground truth by "
                "construction; the correctness gate for RR L1 is determinism vs "
                "the L4/16/64 levels, not this file vs itself.\n")
PY
"$ROOT/langgraph-fastapi/.venv/bin/python" pdf200/validate200.py "$R/rep1-rr/L1" rr $N \
  > "$R/rep1-rr/L1/validation.json" 2>/dev/null
say "RR L1 (from capture): $(head -c 140 $R/rep1-rr/L1/validation.json)"

# 2. probe gate: 10@c4 both arms
say "probe lg 10@c4"
bash pdf200/run_level200.sh lg 4 "$R/probe-lg" $T 10
say "probe rr 10@c4"
bash pdf200/run_level200.sh rr 4 "$R/probe-rr" $T 10
for a in lg rr; do
  say "probe $a: $(head -c 120 $R/probe-$a/validation.json 2>/dev/null)"
done

# 3. RR levels 4, 16, 64 (container NOT restarted — same warm engine that
#    just ran L1; single-pass scope trades clean-state purity for time,
#    recorded in status log)
mkdir -p "$R/rep1-rr"
sh "$ROOT/pdf1k/host_sampler.sh" > "$R/rep1-rr/host_sampler.jsonl" 2>/dev/null &
HS=$!
docker cp "$ROOT/pdf1k/proc_sampler.py" prodbench-rocketride:/tmp/proc_sampler.py >/dev/null
docker exec prodbench-rocketride python /tmp/proc_sampler.py > "$R/rep1-rr/container_sampler.jsonl" 2>/dev/null &
CS=$!
for L in 4 16 64; do
  say "rr L$L starting"
  bash pdf200/run_level200.sh rr $L "$R/rep1-rr/L$L" $T $N
  say "rr L$L: $(head -c 140 $R/rep1-rr/L$L/validation.json 2>/dev/null)"
done
kill $HS $CS 2>/dev/null

# 4. LG all levels (clean restart + prime first)
docker compose restart >/dev/null 2>&1
for i in $(seq 1 120); do
  lg=$(docker inspect -f '{{.State.Health.Status}}' prodbench-langgraph 2>/dev/null)
  rr=$(docker inspect -f '{{.State.Health.Status}}' prodbench-rocketride 2>/dev/null)
  [ "$lg" = healthy ] && [ "$rr" = healthy ] && break; sleep 2
done
curl -s -o /dev/null -F "file=@datasets/govdocs/000009.pdf;type=application/pdf" \
  http://localhost:8100/v1/process/document-pdf-v1
mkdir -p "$R/rep1-lg"
sh "$ROOT/pdf1k/host_sampler.sh" > "$R/rep1-lg/host_sampler.jsonl" 2>/dev/null &
HS=$!
docker cp "$ROOT/pdf1k/proc_sampler.py" prodbench-langgraph:/tmp/proc_sampler.py >/dev/null
docker exec prodbench-langgraph python /tmp/proc_sampler.py > "$R/rep1-lg/container_sampler.jsonl" 2>/dev/null &
CS=$!
for L in 1 4 16 64; do
  say "lg L$L starting"
  bash pdf200/run_level200.sh lg $L "$R/rep1-lg/L$L" $T $N
  say "lg L$L: $(head -c 140 $R/rep1-lg/L$L/validation.json 2>/dev/null)"
done
kill $HS $CS 2>/dev/null

# 5. drift-post
docker exec prodbench-langgraph python -c "
from workload.document.embed import embed_chunks
v = embed_chunks(['The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. How vexingly quick daft zebras jump.'])[0]
print([round(x, 8) for x in v[:8]])" > "$R/drift_post_lg.txt" 2>&1
docker exec prodbench-rocketride python /work/send_one.py /work/data/probe/parity_fixture.txt \
  > "$R/drift_post_rr.txt" 2>&1
say "SINGLE PASS COMPLETE"
