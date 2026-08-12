"""RocketRide open-loop burst: fire ALL docs at once over the WS session.

One connection, one pipe launched with DEFAULT thread settings (no threads=
passed — the question is out-of-the-box defaults). All N send_files calls
are created as concurrent asyncio tasks; the SDK/DAP layer multiplexes or
serializes them — whatever it does IS the default behavior, recorded.

Per doc: submit_ns, completion_ns (== first_result over this protocol),
chunk hashes, vector sanity. Checkpoints continuously. On WS death: one
bounded reconnect+relaunch; docs still pending fail cleanly.

  docker exec prodbench-rocketride python /work/rr_burst.py \
      /work/corpus /work/rep/per_doc.jsonl <timeout_s> [limit_n]
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

PIPE_SRC = Path("/work/benchmark_pdf.pipe")
URI = os.environ.get("ROCKETRIDE_URI", "ws://127.0.0.1:5565/task/service")
APIKEY = os.environ.get("ROCKETRIDE_APIKEY", "local-dev")
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


async def launch(client_holder):
    from rocketride import RocketRideClient

    client = RocketRideClient(uri=URI, auth=APIKEY)
    await client.connect()
    pipe = json.loads(PIPE_SRC.read_text())
    pipe["project_id"] = str(uuid.uuid4())
    stamped = Path(f"/tmp/burst_{pipe['project_id']}.pipe")
    stamped.write_text(json.dumps(pipe))
    used = await client.use(filepath=str(stamped))
    stamped.unlink(missing_ok=True)
    client_holder["client"] = client
    client_holder["token"] = used["token"]
    print(f"[rr-burst] pipe token={used['token']}", flush=True)


async def one(holder, pdf: Path, timeout_s: float, fh, state, lock):
    rec = {"doc": pdf.name, "arm": "rocketride", "ok": False}
    rec["submit_ns"] = time.perf_counter_ns()
    try:
        up = await asyncio.wait_for(
            holder["client"].send_files([(str(pdf), {"doc_id": pdf.stem})],
                                        holder["token"]),
            timeout=timeout_s,
        )
        rec["completion_ns"] = rec["first_result_ns"] = time.perf_counter_ns()
        docs = documents_from(up)
        texts = [d.get("page_content", "") for d in docs]
        vecs = [d.get("embedding") or [] for d in docs]
        finite = all(all(x == x and abs(x) != float("inf") for x in v) for v in vecs)
        rec.update({
            "ok": len(docs) > 0,
            "n_chunks": len(docs),
            "total_chars": sum(len(t) for t in texts),
            "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
            "vector_dim": len(vecs[0]) if vecs and vecs[0] else None,
            "vectors_finite": finite,
            "l2_norms_minmax": [
                round(min((sum(x * x for x in v) ** 0.5 for v in vecs if v), default=0), 6),
                round(max((sum(x * x for x in v) ** 0.5 for v in vecs if v), default=0), 6),
            ],
            "model_id": docs[0].get(MODEL_KEY) if docs else None,
        })
        if not docs:
            rec["error"] = "no documents returned"
            rec["error_raw"] = json.dumps(up, default=str)[:500]
    except asyncio.TimeoutError:
        rec["completion_ns"] = time.perf_counter_ns()
        rec["error"] = f"client timeout after {timeout_s}s"
    except Exception as exc:
        rec["completion_ns"] = time.perf_counter_ns()
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        rec["error_raw"] = repr(getattr(exc, "dap_result", None))[:500]
    async with lock:
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
        state["n"] += 1
        if state["n"] % 25 == 0:
            os.fsync(fh.fileno())
            print(f"[rr-burst] {state['n']} done", flush=True)


async def main(corpus_dir, out_path, timeout_s, limit_n=None):
    docs = sorted(Path(corpus_dir).glob("*.pdf"))
    if limit_n:
        docs = docs[:limit_n]
    holder = {}
    await launch(holder)
    fh = open(out_path, "a")
    state = {"n": 0}
    lock = asyncio.Lock()
    t0 = time.perf_counter_ns()
    tasks = [asyncio.create_task(one(holder, p, timeout_s, fh, state, lock))
             for p in docs]
    t_send1 = time.perf_counter_ns()
    await asyncio.gather(*tasks)
    t_end = time.perf_counter_ns()
    try:
        await holder["client"].terminate(holder["token"])
        await holder["client"].disconnect()
    except Exception:
        pass
    meta = {
        "kind": "client_meta", "arm": "rocketride", "n_docs": len(docs),
        "send_window_task_creation_s": (t_send1 - t0) / 1e9,
        "batch_span_s": (t_end - t0) / 1e9,
        "note": "all send_files calls issued concurrently on one WS session; "
                "SDK/DAP layer decides multiplexing — default behavior",
    }
    fh.write(json.dumps(meta) + "\n")
    fh.close()
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], float(sys.argv[3]),
                     int(sys.argv[4]) if len(sys.argv) > 4 else None))
