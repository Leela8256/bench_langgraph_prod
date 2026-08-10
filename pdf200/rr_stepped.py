"""RocketRide stepped closed-loop driver — warm pool, host-side.

Pool of N WebSocket clients (N from stage0_decision.json), all attached to
the SAME pipeline via use_existing=True + shared project_id. Each slot is
warmed with a fixed tiny doc and must return a VERIFIED completion before
any measurement. Doc i routes to client (i mod N); a global semaphore holds
total in-flight at the level. Completion = proof: parsed chunks+vectors
persisted and identity checked (response filepath == sent file). No proof
within the frozen timeout -> FAILED reason=completion_proof_missing.

  .rrclient/bin/python pdf200/rr_stepped.py <corpus> <out_dir> <level> \
      <timeout_s> <n_docs> [--warmup-only]
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path("/work")
URI = "ws://127.0.0.1:5565/task/service"
APIKEY = "local-dev"
PIPE_SRC = ROOT / "benchmark_pdf.pipe"
STATE = ROOT / "pdf200" / "rr_pool_state.json"
WARMUP_DOC = ROOT / "data" / "probe" / "sample.pdf"
DECISION = ROOT / "pdf200" / "stage0_decision.json"
MODEL_KEY = "embedding_model"


def documents_from(result):
    if isinstance(result, dict):
        docs = result.get("documents")
        if isinstance(docs, list) and all(isinstance(d, dict) for d in docs):
            return docs
        for k in ("result", "data", "output"):
            if k in result:
                got = documents_from(result[k])
                if got:
                    return got
        return []
    if isinstance(result, list):
        out = []
        for item in result:
            out.extend(documents_from(item))
        return out
    return []


async def make_pool(n):
    """N connected clients sharing ONE pipeline (same project_id file)."""
    from rocketride import RocketRideClient

    if STATE.exists():
        pipe_path = Path(json.loads(STATE.read_text())["pipe_path"])
    else:
        pipe = json.loads(PIPE_SRC.read_text())
        pipe["project_id"] = str(uuid.uuid4())
        pipe_path = ROOT / "pdf200" / "shared_pipeline.pipe"
        pipe_path.write_text(json.dumps(pipe))
        STATE.write_text(json.dumps({"pipe_path": str(pipe_path),
                                     "project_id": pipe["project_id"]}))
    pool = []
    for i in range(n):
        c = RocketRideClient(uri=URI, auth=APIKEY)
        await c.connect()
        used = await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        pool.append({"client": c, "token": used["token"], "slot": i,
                     "use_result": used})
    return pool


def verify_docs(docs, min_chunks=1):
    if len(docs) < min_chunks:
        return False, "no documents"
    for d in docs:
        v = d.get("embedding") or []
        if len(v) != 384:
            return False, f"dim={len(v)}"
        if not all(x == x and abs(x) != float("inf") for x in v):
            return False, "non-finite"
    return True, ""


async def warmup(pool, out_dir):
    """Every slot must produce a verified completion. Abort otherwise."""
    results = []
    for p in pool:
        t0 = time.perf_counter()
        up = await asyncio.wait_for(
            p["client"].send_files([(str(WARMUP_DOC), {"doc_id": f"warm-{p['slot']}"})],
                                   p["token"]), timeout=120)
        docs = documents_from(up)
        ok, why = verify_docs(docs)
        results.append({"slot": p["slot"], "ok": ok, "why": why,
                        "wall_s": round(time.perf_counter() - t0, 2),
                        "n_chunks": len(docs)})
        if not ok:
            raise SystemExit(f"WARMUP FAILED slot {p['slot']}: {why} — aborting")
    (Path(out_dir) / "warmup.json").write_text(json.dumps(results))
    print(f"[rr] warmup ok: {len(pool)} slots", flush=True)


async def run_level(pool, corpus, out_dir, level, timeout_s, n_docs):
    docs = sorted(Path(corpus).glob("*.pdf"))[:n_docs]
    out = Path(out_dir)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    fh = open(out / "per_doc.jsonl", "a")
    sem = asyncio.Semaphore(level)
    lock = asyncio.Lock()
    state = {"n": 0}
    t_level0 = time.perf_counter_ns()

    async def one(i, pdf: Path):
        slot = pool[i % len(pool)]
        async with sem:
            rec = {"doc": pdf.name, "arm": "rocketride", "level": level,
                   "slot": slot["slot"], "ok": False,
                   "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
            rec["submit_ns"] = time.perf_counter_ns()
            try:
                up = await asyncio.wait_for(
                    slot["client"].send_files([(str(pdf), {"doc_id": pdf.stem})],
                                              slot["token"]),
                    timeout=timeout_s)
                rec["completion_ns"] = rec["first_result_ns"] = time.perf_counter_ns()
                # identity: the SDK returns per-file results keyed by filepath
                items = [u for u in (up if isinstance(up, list) else [])
                         if isinstance(u, dict) and u.get("filepath") == str(pdf)]
                rec["identity_ok"] = bool(items)
                docs_r = documents_from(items if items else up)
                okv, why = verify_docs(docs_r)
                texts = [d.get("page_content", "") for d in docs_r]
                rec.update({
                    "ok": okv and bool(items),
                    "n_chunks": len(docs_r),
                    "total_chars": sum(len(t) for t in texts),
                    "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest()
                                     for t in texts],
                    "vector_dim": 384 if okv else None,
                    "l2_norms_minmax": [
                        round(min((sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                                   for d in docs_r), default=0), 6),
                        round(max((sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                                   for d in docs_r), default=0), 6)],
                    "model_id": docs_r[0].get(MODEL_KEY) if docs_r else None,
                })
                if not rec["ok"]:
                    rec["error"] = f"completion_proof_missing: {why or 'identity'}"
                (raw / f"L{level}_{pdf.stem}.json").write_text(
                    json.dumps(up, default=str))
            except asyncio.TimeoutError:
                rec["completion_ns"] = time.perf_counter_ns()
                rec["error"] = "FAILED, reason=completion_proof_missing (timeout)"
            except Exception as exc:
                rec["completion_ns"] = time.perf_counter_ns()
                rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
            async with lock:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                state["n"] += 1
                if state["n"] % 25 == 0:
                    os.fsync(fh.fileno())
                    print(f"[rr L{level}] {state['n']}/{len(docs)}", flush=True)

    await asyncio.gather(*[one(i, p) for i, p in enumerate(docs)])
    meta = {"kind": "level_meta", "arm": "rocketride", "level": level,
            "n_docs": len(docs), "pool": len(pool),
            "level_span_s": (time.perf_counter_ns() - t_level0) / 1e9}
    fh.write(json.dumps(meta) + "\n")
    fh.close()
    print(json.dumps(meta), flush=True)


async def main():
    corpus, out_dir, level, timeout_s, n_docs = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]),
        int(sys.argv[5]))
    n_pool = json.loads(DECISION.read_text())["pool_size"]
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    pool = await make_pool(n_pool)
    try:
        await warmup(pool, out_dir)
        if "--warmup-only" not in sys.argv:
            await run_level(pool, corpus, out_dir, level, timeout_s, n_docs)
    finally:
        for p in pool:
            try:
                await p["client"].disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
