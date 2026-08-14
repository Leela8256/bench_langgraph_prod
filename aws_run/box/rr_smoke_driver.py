"""RocketRide smoke driver — runs INSIDE the prodbench-rocketride container.

Why inside: the engine rejects WebSocket upgrades through Docker's published
port (CONTEXT_SNAPSHOT 4.6) -- ws://localhost:5565 from the host fails while
the identical call works container-internally. Every RR driver therefore runs
container-side. This is also a product finding in its own right.

Writes per_doc.jsonl in the schema metrics/ consumes, satisfying the RR record
contract from metrics.m0_correctness.REQUIRED_TRUE["rr"] (identity_ok). Vector
finiteness is folded into vector_dim exactly as gate50/rr_native_gate.py does:
a non-finite vector can never reach a dim of 384.

Sequential closed-loop to match the LangGraph arm -- true service latency.

  python3 rr_smoke_driver.py <corpus_dir> <out_dir> <n_docs>
"""

import asyncio
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

URI = "ws://127.0.0.1:5565/task/service"
APIKEY = "local-dev"
PIPE_SRC = Path("/work/benchmark_pdf.pipe")
WARMUP_DOC = Path("/work/data/probe/sample.pdf")
TIMEOUT_S = 300
EMBED_DIM = 384
# Client sockets, not offered concurrency. gate-50 used 8; going higher risks
# measuring the client's socket handling instead of the engine.
POOL_MAX = 8


def documents_from(result):
    """Unwrap the engine's nested response shapes. Same logic as
    gate50/rr_native_gate.py -- do not simplify without re-checking a raw
    capture; the nesting varies by node."""
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


def verify(docs):
    if not docs:
        return False, "no documents"
    for d in docs:
        v = d.get("embedding") or []
        if len(v) != EMBED_DIM:
            return False, f"dim={len(v)}"
        if not all(x == x and abs(x) != float("inf") for x in v):
            return False, "non-finite"
    return True, ""


async def main():
    from rocketride import RocketRideClient

    corpus_dir, out, n = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    corpus = sorted(corpus_dir.glob("*.pdf"))[:n]
    if not corpus:
        raise SystemExit(f"no PDFs in {corpus_dir}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps({"docs": [p.name for p in corpus], "n": len(corpus)}))

    pipe = json.loads(PIPE_SRC.read_text())
    pipe["project_id"] = str(uuid.uuid4())
    pipe_path = out / "pipeline.pipe"
    pipe_path.write_text(json.dumps(pipe))

    client = RocketRideClient(uri=URI, auth=APIKEY)
    await client.connect()
    used = await client.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
    token = used["token"]
    print(f"[rr] pipeline up, token={token}", flush=True)

    # Uncounted warm-up: the first document through a cold pipe pays engine
    # spin-up. Excluding it here is separate from M1's warm_n window.
    up = await asyncio.wait_for(
        client.send_files([(str(WARMUP_DOC), {"doc_id": "warm"})], token),
        timeout=120)
    ok, why = verify(documents_from(up))
    if not ok:
        raise SystemExit(f"WARMUP FAILED: {why}")
    print("[rr] warmup ok", flush=True)

    mode = sys.argv[4] if len(sys.argv) > 4 else "seq"
    if mode == "seq":
        offered = 1
    elif mode == "blast":
        offered = len(corpus)
    elif mode.startswith("c") and mode[1:].isdigit():
        offered = int(mode[1:])
    else:
        raise SystemExit(f"bad mode {mode!r}: expected seq | blast | c<N>")

    # Sampler ts is wall-clock epoch, per-doc ts is monotonic; this offset is
    # what lets m7_resources.window() slice the sampler to EXACTLY the
    # throughput window.
    mono_offset_ns = time.time_ns() - time.perf_counter_ns()

    # A pool of clients, not one: a single WebSocket serialises its own
    # requests, so concurrency would be a fiction. Pool is capped -- one client
    # per doc at blast would open 150 sockets and measure the client, not the
    # engine.
    pool_size = min(offered, POOL_MAX)
    pool = [client]
    for _ in range(pool_size - 1):
        c = RocketRideClient(uri=URI, auth=APIKEY)
        await c.connect()
        await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        pool.append(c)
    print(f"[rr] mode={mode} offered={offered} pool={len(pool)}", flush=True)

    sem = asyncio.Semaphore(offered)
    done = [0]

    async def one(i, pdf):
        async with sem:
            cl = pool[i % len(pool)]
            rec = {"doc": pdf.name, "arm": "rocketride-docker-3.3.1", "ok": False,
                   "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                   "size_bytes": pdf.stat().st_size,
                   "submit_ns": time.perf_counter_ns()}
            try:
                up = await asyncio.wait_for(
                    cl.send_files([(str(pdf), {"doc_id": pdf.stem})], token),
                    timeout=TIMEOUT_S)
                rec["completion_ns"] = time.perf_counter_ns()
                items = [u for u in (up if isinstance(up, list) else [])
                         if isinstance(u, dict) and u.get("filepath") == str(pdf)]
                rec["identity_ok"] = bool(items)
                docs = documents_from(items if items else up)
                okv, why = verify(docs)
                texts = [d.get("page_content", "") for d in docs]
                rec["n_chunks"] = len(docs)
                rec["total_chars"] = sum(len(t) for t in texts)
                rec["chunk_sha256"] = [
                    hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
                rec["vector_dim"] = EMBED_DIM if okv else None
                norms = [sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                         for d in docs]
                rec["l2_norms_minmax"] = ([round(min(norms), 6), round(max(norms), 6)]
                                          if norms else None)
                rec["ok"] = bool(okv and items)
                if rec["ok"]:
                    rec["reason"] = "completed"
                elif not docs:
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
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(corpus):
                print(f"[rr] {done[0]}/{len(corpus)}", flush=True)
            return rec

    t0 = time.perf_counter_ns()
    records = await asyncio.gather(*[one(i, p) for i, p in enumerate(corpus)])
    span = (time.perf_counter_ns() - t0) / 1e9

    with open(out / "per_doc.jsonl", "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        fh.write(json.dumps({
            "kind": "shot_meta", "arm": "rocketride-docker-3.3.1", "mode": mode,
            "n_docs": len(corpus), "span_s": round(span, 2),
            "timeout_s": TIMEOUT_S,
            "offered_concurrency": offered,
            "client_pool": len(pool),
            "configured_concurrency_note":
                "engine threadCount is engine-internal; recorded from the "
                "engine probe, never assumed from config",
            "mono_offset_ns": mono_offset_ns,
        }) + "\n")

    for c in pool:
        try:
            await c.disconnect()
        except Exception:
            pass
    ok_n = sum(1 for r in records if r.get("ok"))
    print(f"[rr] done in {span:.1f}s ok={ok_n}/{len(records)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
