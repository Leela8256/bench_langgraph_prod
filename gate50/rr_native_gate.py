"""Gate-50 RocketRide driver — NATIVE engine (darwin-arm64, no Docker).

Blast mode: all 50 docs fired at once across a warm pool of 8 WS clients
(slot = i mod 8), no client-side cap, fixed 300 s per-doc timeout.
Uncounted slot warmup first (1 tiny doc per slot, verified). At the end,
embeds the cross-arm parity fixture through the pipe (uncounted).

Run:  .rrclient/bin/python gate50/rr_native_gate.py
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URI = "ws://127.0.0.1:5565/task/service"
APIKEY = "local-dev"
import sys as _sys
_args = dict(enumerate(_sys.argv))
OUT = Path(_args.get(2) or ROOT / "gate50" / "out_rr")
CORPUS = sorted(Path(_args.get(1) or ROOT / "datasets" / "govdocs").glob("*.pdf"))[:int(_args.get(4) or 50)]
MODE = _args.get(3, "blast")  # blast | seq | c<N>
WARMUP_DOC = ROOT / "rocketride" / "data" / "probe" / "sample.pdf"
PARITY_TXT = ROOT / "rocketride" / "data" / "probe" / "parity_fixture.txt"
POOL = 8
TIMEOUT_S = 300
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


def verify_docs(docs):
    if not docs:
        return False, "no documents"
    for d in docs:
        v = d.get("embedding") or []
        if len(v) != 384:
            return False, f"dim={len(v)}"
        if not all(x == x and abs(x) != float("inf") for x in v):
            return False, "non-finite"
    return True, ""


async def main():
    from rocketride import RocketRideClient

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)

    pipe = json.loads((ROOT / "rocketride" / "benchmark_pdf.pipe").read_text())
    pipe["project_id"] = str(uuid.uuid4())
    pipe_path = OUT / "pipeline.pipe"
    pipe_path.write_text(json.dumps(pipe))

    pool = []
    for i in range(POOL):
        c = RocketRideClient(uri=URI, auth=APIKEY)
        await c.connect()
        used = await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        pool.append({"client": c, "token": used["token"], "slot": i})
    print(f"[rr-native] pool of {POOL} up, token={pool[0]['token']}", flush=True)

    for p in pool:
        up = await asyncio.wait_for(
            p["client"].send_files([(str(WARMUP_DOC), {"doc_id": f"warm-{p['slot']}"})],
                                   p["token"]), timeout=120)
        ok, why = verify_docs(documents_from(up))
        if not ok:
            raise SystemExit(f"WARMUP FAILED slot {p['slot']}: {why}")
    print(f"[rr-native] warmup ok: {POOL}/{POOL} slots", flush=True)

    fh = open(OUT / "per_doc.jsonl", "w")
    lock = asyncio.Lock()
    state = {"n": 0}

    async def one(i, pdf):
        slot = pool[i % POOL]
        rec = {"doc": pdf.name, "arm": "rocketride-native-3.3.1", "slot": slot["slot"],
               "ok": False,
               "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
        rec["submit_ns"] = time.perf_counter_ns()
        try:
            up = await asyncio.wait_for(
                slot["client"].send_files([(str(pdf), {"doc_id": pdf.stem})],
                                          slot["token"]), timeout=TIMEOUT_S)
            rec["completion_ns"] = time.perf_counter_ns()
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
                "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
                "vector_dim": 384 if okv else None,
                "l2_norms_minmax": [
                    round(min((sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                               for d in docs_r), default=0), 6),
                    round(max((sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                               for d in docs_r), default=0), 6)],
                "model_id": docs_r[0].get(MODEL_KEY) if docs_r else None,
            })
            if rec["ok"]:
                rec["reason"] = "completed"
                (OUT / "raw" / f"{pdf.stem}.json").write_text(json.dumps(up, default=str))
            elif not docs_r:
                rec["reason"] = "no_documents"
                rec["error_raw"] = json.dumps(up, default=str)[:400]
            else:
                rec["reason"] = "completion_proof_missing"
                rec["error"] = why or "identity"
        except asyncio.TimeoutError:
            rec["completion_ns"] = time.perf_counter_ns()
            rec["reason"] = "timeout"
        except Exception as exc:
            rec["completion_ns"] = time.perf_counter_ns()
            rec["reason"] = "transport_error"
            rec["error"] = f"{type(exc).__name__}: {exc}"[:250]
        async with lock:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            state["n"] += 1
            if state["n"] % 10 == 0:
                os.fsync(fh.fileno())
                print(f"[rr-native] {state['n']}/50", flush=True)

    limit = None
    if MODE == "seq":
        limit = 1
    elif MODE.startswith("c") and MODE[1:].isdigit():
        limit = int(MODE[1:])
    sem = asyncio.Semaphore(limit) if limit else None

    async def gated(i, p):
        if sem:
            async with sem:
                await one(i, p)
        else:
            await one(i, p)

    t0 = time.perf_counter_ns()
    await asyncio.gather(*[gated(i, p) for i, p in enumerate(CORPUS)])
    span = (time.perf_counter_ns() - t0) / 1e9

    # parity fixture (uncounted)
    parity = None
    try:
        up = await asyncio.wait_for(
            pool[0]["client"].send_files([(str(PARITY_TXT), {"doc_id": "parity"})],
                                         pool[0]["token"]), timeout=120)
        d = documents_from(up)
        parity = d[0].get("embedding") if d else None
    except Exception as exc:
        parity = f"failed: {exc}"
    (OUT / "parity_vector.json").write_text(json.dumps({"vector": parity}))

    meta = {"kind": "shot_meta", "arm": "rocketride-native-3.3.1",
            "mode": MODE, "n_docs": len(CORPUS), "pool": POOL,
            "span_s": round(span, 2), "timeout_s": TIMEOUT_S}
    fh.write(json.dumps(meta) + "\n")
    fh.close()
    for p in pool:
        try:
            await p["client"].disconnect()
        except Exception:
            pass
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
