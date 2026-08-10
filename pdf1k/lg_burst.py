"""LangGraph open-loop burst: fire ALL docs at once, no client-side cap.

Per doc: submit_ns (request task actually started), completion_ns, status,
X-Output-SHA256 verification, chunk hashes, vector sanity. Non-streaming
HTTP means first-result == completion; both recorded for the fixed metric
definitions. Checkpoints JSONL continuously; fsyncs every 25 records.

Client self-instrumentation: rusage CPU + send-window duration; a saturated
client flags the rep as compromised.

  .venv python pdf1k/lg_burst.py <corpus_dir> <out_jsonl> <timeout_s> [limit_n]
"""

import asyncio
import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8100"
PIPELINE = "document-pdf-v1"


async def one(client, pdf: Path, timeout_s: float, fh, fsync_every, state):
    rec = {"doc": pdf.name, "arm": "langgraph", "ok": False}
    data = pdf.read_bytes()
    rec["submit_ns"] = time.perf_counter_ns()
    try:
        resp = await asyncio.wait_for(
            client.post(
                f"/v1/process/{PIPELINE}",
                files={"file": (pdf.name, data, "application/pdf")},
                data={"request_id": f"pdf1k-{pdf.stem}"},
            ),
            timeout=timeout_s,
        )
        rec["completion_ns"] = rec["first_result_ns"] = time.perf_counter_ns()
        rec["http_status"] = resp.status_code
        body = resp.content
        claimed = resp.headers.get("x-output-sha256")
        rec["sha_header_ok"] = (
            claimed == hashlib.sha256(body).hexdigest() if claimed else None
        )
        if resp.status_code == 200:
            out = json.loads(body)["output"]
            texts = [c["text"] for c in out["chunks"]]
            vecs = out["vectors"]
            finite = all(all(x == x and abs(x) != float("inf") for x in v) for v in vecs)
            rec.update({
                "ok": True,
                "n_chunks": len(texts),
                "total_chars": sum(len(t) for t in texts),
                "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
                "offsets": [[c["start"], c["end"]] for c in out["chunks"]],
                "vector_dim": len(vecs[0]) if vecs else None,
                "vectors_finite": finite,
                "l2_norms_minmax": [
                    round(min((sum(x * x for x in v) ** 0.5 for v in vecs), default=0), 6),
                    round(max((sum(x * x for x in v) ** 0.5 for v in vecs), default=0), 6),
                ],
            })
        else:
            try:
                rec["error"] = json.loads(body)["error"]["code"]
                rec["error_raw"] = body.decode(errors="replace")[:500]
            except Exception:
                rec["error"] = body[:200].decode(errors="replace")
    except asyncio.TimeoutError:
        rec["completion_ns"] = time.perf_counter_ns()
        rec["error"] = f"client timeout after {timeout_s}s"
    except Exception as exc:
        rec["completion_ns"] = time.perf_counter_ns()
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    fh.write(json.dumps(rec) + "\n")
    fh.flush()
    state["n"] += 1
    if state["n"] % fsync_every == 0:
        os.fsync(fh.fileno())
        print(f"[lg-burst] {state['n']} done", flush=True)


async def main(corpus_dir, out_path, timeout_s, limit_n=None):
    docs = sorted(Path(corpus_dir).glob("*.pdf"))
    if limit_n:
        docs = docs[:limit_n]
    fh = open(out_path, "a")
    state = {"n": 0}
    t_cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    limits = httpx.Limits(max_connections=len(docs) + 10,
                          max_keepalive_connections=len(docs) + 10)
    async with httpx.AsyncClient(base_url=BASE, limits=limits, timeout=None) as client:
        r = await client.get("/health/ready")
        assert r.status_code == 200, "server not ready"
        t_send0 = time.perf_counter_ns()
        tasks = [asyncio.create_task(one(client, p, timeout_s, fh, 25, state))
                 for p in docs]
        t_send1 = time.perf_counter_ns()  # all tasks created (send window open)
        await asyncio.gather(*tasks)
        t_end = time.perf_counter_ns()
    t_cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    fh.close()
    meta = {
        "kind": "client_meta", "arm": "langgraph", "n_docs": len(docs),
        "send_window_task_creation_s": (t_send1 - t_send0) / 1e9,
        "batch_span_s": (t_end - t_send0) / 1e9,
        "client_cpu_user_s": round(t_cpu1.ru_utime - t_cpu0.ru_utime, 2),
        "client_cpu_sys_s": round(t_cpu1.ru_stime - t_cpu0.ru_stime, 2),
        "client_maxrss_mb": round(t_cpu1.ru_maxrss / 1048576, 1),
        "note": "send window = asyncio task creation; actual socket writes "
                "interleave with responses under the single event loop",
    }
    with open(out_path, "a") as fh2:
        fh2.write(json.dumps(meta) + "\n")
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], float(sys.argv[3]),
                     int(sys.argv[4]) if len(sys.argv) > 4 else None))
