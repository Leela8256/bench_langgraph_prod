#!/bin/bash
# gt-rr -> ground truth file -> probe(10@c4) -> calibration(50@c4, clean
# state) -> freeze timeouts -> drift-pre fixtures -> exec chain200.sh
set -eu
ROOT="/Users/leelaprasaddammalapati/Desktop/prod bench"
cd "$ROOT"
say() { echo "[pre200 $(date +%H:%M:%S)] $*"; }

# 1. RR ground truth from the capture pass
docker cp prodbench-rocketride:/work/out/gt-rr runs/pdf200/gt-rr >/dev/null 2>&1 || true
/usr/bin/python3 - <<'PY'
import json
rows=[json.loads(l) for l in open("runs/pdf200/gt-rr/per_doc.jsonl")]
docs=[r for r in rows if r.get("kind")!="level_meta"]
ok=[r for r in docs if r.get("ok")]
with open("results200/ground_truth_rr.jsonl","w") as out:
    for r in ok:
        out.write(json.dumps({"doc":r["doc"],"chunk_sha256":r["chunk_sha256"],
                              "n_chunks":r["n_chunks"],"total_chars":r["total_chars"]})+"\n")
fails=[r["doc"] for r in docs if not r.get("ok")]
print(f"RR ground truth: {len(ok)}/200 captured; excluded (failed capture): {fails}")
PY

# 2. probe: 10 docs at concurrency 4, both arms (gates must pass)
say "probe lg 10@c4"
bash pdf200/run_level200.sh lg 4 runs/pdf200/probe-lg 300 10
say "probe rr 10@c4"
bash pdf200/run_level200.sh rr 4 runs/pdf200/probe-rr 300 10
for a in lg rr; do
  V=$(/usr/bin/python3 -c "import json;d=json.load(open('runs/pdf200/probe-$a/validation.json'));print(d['n_ok'],d.get('valid'))" 2>/dev/null)
  say "probe $a: n_ok/valid = $V"
done

# 3. calibration: clean restart + warmup, 50@c4 per arm
for a in rr lg; do
  say "calibration $a 50@c4 (clean state)"
  docker compose restart >/dev/null 2>&1
  for i in $(seq 1 120); do
    lg=$(docker inspect -f '{{.State.Health.Status}}' prodbench-langgraph 2>/dev/null)
    rr=$(docker inspect -f '{{.State.Health.Status}}' prodbench-rocketride 2>/dev/null)
    [ "$lg" = healthy ] && [ "$rr" = healthy ] && break; sleep 2
  done
  if [ "$a" = lg ]; then
    curl -s -o /dev/null -F "file=@datasets/govdocs/000009.pdf;type=application/pdf" \
      http://localhost:8100/v1/process/document-pdf-v1
  else
    docker exec prodbench-rocketride sh -c 'mkdir -p /work/out/warm' >/dev/null
    docker exec prodbench-rocketride python /work/rr_stepped.py /work/corpus /work/out/warm 1 120 1 --warmup-only >/dev/null 2>&1
  fi
  bash pdf200/run_level200.sh "$a" 4 "runs/pdf200/cal-$a" 300 50
  say "cal $a: $(head -c 160 runs/pdf200/cal-$a/validation.json 2>/dev/null)"
done

# 4. freeze
/usr/bin/python3 - <<'PY'
import json
def p99(path):
    lats=[]
    for l in open(path):
        r=json.loads(l)
        if r.get("kind")=="level_meta": continue
        if "submit_ns" in r and "completion_ns" in r:
            lats.append((r["completion_ns"]-r["submit_ns"])/1e9)
    lats.sort()
    return lats[min(int(len(lats)*0.99),len(lats)-1)] if lats else None
lg=p99("runs/pdf200/cal-lg/per_doc.jsonl"); rr=p99("runs/pdf200/cal-rr/per_doc.jsonl")
frozen={"formula":"max(60, cal p99 x 5) per arm; FROZEN",
        "lg_cal_p99_s":round(lg,2) if lg else None,
        "rr_cal_p99_s":round(rr,2) if rr else None,
        "lg_timeout_s":max(60.0,round(lg*5,1)) if lg else 60.0,
        "rr_timeout_s":max(60.0,round(rr*5,1)) if rr else 60.0,
        "levels":[1,4,16,64],"n_docs":200,"pool_size":8}
json.dump(frozen,open("runs/pdf200/frozen_params.json","w"),indent=1)
print(json.dumps(frozen))
PY

# 5. drift-pre fixtures
docker exec prodbench-langgraph python -c "
from workload.document.embed import embed_chunks
v = embed_chunks(['The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. How vexingly quick daft zebras jump.'])[0]
print([round(x, 8) for x in v[:8]])" > runs/pdf200/drift_pre_lg.txt 2>&1
docker exec prodbench-rocketride python /work/send_one.py /work/data/probe/parity_fixture.txt \
  > runs/pdf200/drift_pre_rr.txt 2>&1
say "drift-pre captured"

# 6. hand off to the rep chain
exec bash pdf200/chain200.sh
