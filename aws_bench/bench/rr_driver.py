"""RocketRide driver — runs in the bench CLIENT container.

Runs in the bench CLIENT container, never inside the engine's container: the
client's own CPU and RSS must not land in the server's cgroup, and both arms
must receive their bytes over an equivalent network hop.

Writes per_doc.jsonl in the schema metrics/ consumes, satisfying the RR record
contract from metrics.m0_correctness.REQUIRED_TRUE["rr"] (identity_ok). Vector
finiteness is folded into vector_dim exactly as gate50/rr_native_gate.py does:
a non-finite vector can never reach a dim of 384.

Modes: seq (closed-loop, true service latency); blast (ONE batched
send_files -- the engine holds the backlog and schedules it); c<N>.

  python3 rr_smoke_driver.py <corpus_dir> <out_dir> <n_docs>
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Reached over the network from the bench client container, NOT from inside
# the engine's own container -- see bench.Dockerfile for why that matters.
URI = os.environ.get("ROCKETRIDE_URI", "ws://rocketride:5565/task/service")
APIKEY = os.environ.get("ROCKETRIDE_APIKEY", "local-dev")
PIPE_SRC = Path(os.environ.get("BENCH_PIPE", "/pipe/benchmark_pdf.pipe"))
WARMUP_DOC = Path(os.environ.get("BENCH_WARMUP_DOC", "/pipe/sample.pdf"))
TIMEOUT_S = int(os.environ.get("BENCH_TIMEOUT_S", "3600"))
# ONE deadline for both arms and both modes. Previously LangGraph had 300s
# per request while a RocketRide batch had 3600s, so a healthy LangGraph doc
# late in a 200-doc backlog could be killed by queue wait while the equivalent
# RocketRide batch ran on for an hour. Not a matched condition.
BATCH_TIMEOUT_S = int(os.environ.get("BENCH_TIMEOUT_S", "3600"))
EMBED_DIM = 384
# Client sockets, not offered concurrency. Default 0 = no client-side cap
# (one socket per in-flight doc), so the engine's scheduler is the only
# limiter. Set RR_POOL_MAX to reimpose one.
POOL_MAX = int(os.environ.get("RR_POOL_MAX", "0")) or 10 ** 9


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


def records_from_batch(corpus, out, t0_ns, mono_offset_ns):
    """Per-doc records from ONE batched send_files() response.

    Timing note, stated in every record: a batch has one submit instant and
    one return instant, so there are no measured per-document timestamps.
    submit_ns is the batch start (true -- every file WAS submitted then) and
    completion_ns is derived from the engine's own per-file `upload_time`.
    `timing_source` marks this so a derived value is never mistaken for a
    measured one. The batch span itself is measured exactly, so M1 is exact;
    only per-doc M2 is an approximation.

    Attribution is by filepath basename, not list position: position-based
    zip silently mis-credits work if the engine reorders. A file with no
    matching response is recorded as a failure, never dropped.
    """
    items = out if isinstance(out, list) else [out]
    by_name = {}
    for it in items:
        if isinstance(it, dict):
            fp = it.get("filepath")
            if isinstance(fp, str) and fp:
                by_name.setdefault(Path(fp).name, it)

    recs = []
    for pdf in corpus:
        it = by_name.get(pdf.name)
        rec = {"doc": pdf.name, "arm": "rocketride-docker-3.3.1", "ok": False,
               "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
               "size_bytes": pdf.stat().st_size,
               "submit_ns": t0_ns,
               "timing_source": "batch_upload_time (derived, not measured)"}
        if it is None:
            rec["completion_ns"] = t0_ns
            rec["identity_ok"] = False
            rec["reason"] = "no_response_for_file"
            recs.append(rec)
            continue
        ut = it.get("upload_time")
        rec["completion_ns"] = t0_ns + int(float(ut) * 1e9) if isinstance(
            ut, (int, float)) else t0_ns
        rec["upload_time_s"] = ut
        rec["identity_ok"] = True          # matched by filepath basename
        docs = documents_from(it)
        okv, why = verify(docs)
        texts = [d.get("page_content", "") for d in docs]
        rec["n_chunks"] = len(docs)
        rec["total_chars"] = sum(len(t) for t in texts)
        rec["chunk_sha256"] = [hashlib.sha256(t.encode("utf-8")).hexdigest()
                               for t in texts]
        rec["vector_dim"] = EMBED_DIM if okv else None
        norms = [sum(x * x for x in (d.get("embedding") or [])) ** 0.5
                 for d in docs]
        rec["l2_norms_minmax"] = ([round(min(norms), 6), round(max(norms), 6)]
                                  if norms else None)
        rec["ok"] = bool(okv)
        if rec["ok"]:
            rec["reason"] = "completed"
        elif not docs:
            rec["reason"] = "no_documents"
            rec["error_raw"] = json.dumps(it, default=str)[:400]
        else:
            rec["reason"] = "completion_proof_missing"
            rec["error"] = why
        recs.append(rec)
    return recs


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

    # RR_THREADS unset -> the engine's own default pool (what an untuned
    # deployment gets). Set -> that pool size is requested explicitly.
    #
    # Two prior runs measured ~5.8-5.9 effective cores on a 32-core box with
    # 372-399 threads alive and client concurrency of 8 then 11 -- parallelism
    # did not move, so the limiter is the engine's pool, not the client. The
    # comparable Haystack-suite run passes threads=<host cores> and reaches
    # 24.28 cores at 76% CPU. This makes that the variable under test.
    rr_threads = os.environ.get("RR_THREADS")
    threads = int(rr_threads) if rr_threads else None

    client = RocketRideClient(uri=URI, auth=APIKEY)
    await client.connect()
    use_kwargs = dict(filepath=str(pipe_path), use_existing=True, ttl=7200)
    if threads:
        use_kwargs["threads"] = threads
    used = await client.use(**use_kwargs)
    token = used["token"]
    print(f"[rr] pipeline up, token={token}, "
          f"threads_requested={threads if threads else 'NONE (engine default)'}",
          flush=True)

    mode = sys.argv[4] if len(sys.argv) > 4 else "seq"
    warm_docs = int(sys.argv[5]) if len(sys.argv) > 5 else 0

    # Warm-start: WARM_DOCS real documents pushed through before the measured
    # span, timed separately and excluded. One tiny fixture is not enough --
    # a cold engine pays model load, JIT and cache fill, and with an unknown
    # pool size a single document leaves most workers cold at t=0.
    warm_s = None
    if warm_docs > 0:
        warm_set = corpus[:warm_docs]
        tw = time.perf_counter_ns()
        await asyncio.wait_for(
            client.send_files([(str(p), {"doc_id": f"warm-{p.stem}"})
                               for p in warm_set], token),
            timeout=TIMEOUT_S)
        warm_s = (time.perf_counter_ns() - tw) / 1e9
        print(f"[rr] warm-start: {len(warm_set)} docs in {warm_s:.1f}s "
              f"(excluded from measurement)", flush=True)
    else:
        up = await asyncio.wait_for(
            client.send_files([(str(WARMUP_DOC), {"doc_id": "warm"})], token),
            timeout=120)
        ok, why = verify(documents_from(up))
        if not ok:
            raise SystemExit(f"WARMUP FAILED: {why}")
        print("[rr] warmup ok (1 fixture doc)", flush=True)

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

    # Client socket pool. Each send_files() call appears to occupy its
    # connection for the round trip, so the pool -- not `offered` -- is the
    # real concurrency ceiling. The 200-doc run at pool=8 measured a 290.7 s
    # span, and 200/8 x 11.2 s (its own p50) = 280 s: the client was the
    # limiter, not the engine. Default is now NO client-side cap, so the
    # engine's own scheduling is what is under test.
    # Blast issues ONE send_files() on ONE client, so a pool is pointless --
    # and building one triggered 189 refused connections (the engine accepts
    # ~11 concurrent). Only the per-doc modes need multiple sockets.
    pool_size = 1 if mode == "blast" else min(offered, POOL_MAX)
    pool = [client]

    async def add_client():
        c = RocketRideClient(uri=URI, auth=APIKEY)
        await c.connect()
        # use_existing + the same project_id: all clients attach to the ONE
        # pipeline instance rather than each spawning a backend.
        await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        return c

    if pool_size > 1:
        t_pool = time.perf_counter_ns()
        # Concurrently: 200 sequential connect+use round trips would take
        # longer than the measured span itself.
        extra = await asyncio.gather(*[add_client() for _ in range(pool_size - 1)],
                                     return_exceptions=True)
        failed = [e for e in extra if isinstance(e, Exception)]
        pool.extend([c for c in extra if not isinstance(c, Exception)])
        print(f"[rr] pool: {len(pool)} clients in "
              f"{(time.perf_counter_ns() - t_pool) / 1e9:.1f}s"
              + (f" ({len(failed)} failed: {type(failed[0]).__name__})" if failed else ""),
              flush=True)
    print(f"[rr] mode={mode} offered={offered} pool={len(pool)} "
          f"(pool_cap={POOL_MAX})", flush=True)

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
    if mode == "blast":
        # ONE call carrying the whole backlog. The engine holds the batch and
        # its own pool schedules it -- this is how the SDK is built to work
        # (send_files gathers one coroutine per file, "let server handle
        # queuing"). Submitting 200 single-file calls instead pins concurrency
        # to the connection count, which the engine caps around 11: measured
        # 5.8-6.0 effective cores across pool=8, pool=11 and threads=32, while
        # the comparable Haystack-suite run reaches 24.28 by batching.
        payload = [(str(p), {"doc_id": p.stem}) for p in corpus]
        # NOT `out`: that name is the output DIRECTORY (a Path) and rebinding
        # it turns the later `out / "per_doc.jsonl"` into list / str.
        batch_result = await asyncio.wait_for(client.send_files(payload, token),
                                              timeout=BATCH_TIMEOUT_S)
        span = (time.perf_counter_ns() - t0) / 1e9
        records = records_from_batch(corpus, batch_result, t0, mono_offset_ns)
        returned = len(batch_result) if isinstance(batch_result, list) else 1
        matched = sum(1 for r in records if r.get("reason") != "no_response_for_file")
        print(f"[rr] batch returned {returned} items for {len(corpus)} files; "
              f"{matched} attributed by filepath", flush=True)
    else:
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
            "client_pool_cap": None if POOL_MAX >= 10 ** 9 else POOL_MAX,
            "threads_requested": threads,
            "warm_docs": warm_docs,
            "warm_s": round(warm_s, 3) if warm_s is not None else None,
            "configured_concurrency_note":
                "threads_requested is what was ASKED FOR; the engine pool actually "
                "used is not observable from here — never conflate the two.",
            "mono_offset_ns": mono_offset_ns,
        }) + "\n")

    # terminate() before disconnect: disconnecting alone leaves the pipeline and
    # its backend running, and a surviving backend holds RSS that starves the
    # next run. Best-effort — a wedged backend may not reap, which is itself
    # worth seeing in the engine log.
    try:
        await client.terminate(token)
    except Exception as exc:
        print(f"[rr] terminate failed: {type(exc).__name__}: {exc}", flush=True)
    for c in pool:
        try:
            await c.disconnect()
        except Exception:
            pass
    ok_n = sum(1 for r in records if r.get("ok"))
    print(f"[rr] done in {span:.1f}s ok={ok_n}/{len(records)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
