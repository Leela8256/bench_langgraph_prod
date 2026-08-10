"""LangGraph stepped closed-loop driver — host-side HTTP client.

Semaphore(level) holds total in-flight; deterministic doc order. Completion
= HTTP 200 + X-Output-SHA256 verified + payload parsed and persisted +
request_id echo matches. Raw responses kept.

  .venv python pdf200/lg_stepped.py <corpus> <out_dir> <level> <timeout_s> <n_docs>
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


async def main():
    corpus, out_dir, level, timeout_s, n_docs = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]),
        int(sys.argv[5]))
    docs = sorted(Path(corpus).glob("*.pdf"))[:n_docs]
    out = Path(out_dir)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    fh = open(out / "per_doc.jsonl", "a")
    sem = asyncio.Semaphore(level)
    lock = asyncio.Lock()
    state = {"n": 0}
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)

    limits = httpx.Limits(max_connections=level + 5,
                          max_keepalive_connections=level + 5)
    async with httpx.AsyncClient(base_url=BASE, limits=limits,
                                 timeout=None) as client:
        r = await client.get("/health/ready")
        assert r.status_code == 200, "server not ready"
        t_level0 = time.perf_counter_ns()

        async def one(pdf: Path):
            async with sem:
                rid = f"pdf200-{pdf.stem}"
                rec = {"doc": pdf.name, "arm": "langgraph", "level": level,
                       "ok": False,
                       "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
                rec["submit_ns"] = time.perf_counter_ns()
                try:
                    resp = await asyncio.wait_for(
                        client.post(f"/v1/process/{PIPELINE}",
                                    files={"file": (pdf.name, pdf.read_bytes(),
                                                    "application/pdf")},
                                    data={"request_id": rid}),
                        timeout=timeout_s)
                    rec["completion_ns"] = rec["first_result_ns"] = time.perf_counter_ns()
                    rec["http_status"] = resp.status_code
                    body = resp.content
                    claimed = resp.headers.get("x-output-sha256")
                    sha_ok = claimed == hashlib.sha256(body).hexdigest()
                    if resp.status_code == 200 and sha_ok:
                        parsed = json.loads(body)
                        ident = parsed.get("request_id") == rid
                        outp = parsed["output"]
                        texts = [c["text"] for c in outp["chunks"]]
                        vecs = outp["vectors"]
                        finite = all(all(x == x and abs(x) != float("inf") for x in v)
                                     for v in vecs)
                        rec.update({
                            "ok": ident and finite,
                            "identity_ok": ident,
                            "sha_header_ok": True,
                            "n_chunks": len(texts),
                            "total_chars": sum(len(t) for t in texts),
                            "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest()
                                             for t in texts],
                            "offsets": [[c["start"], c["end"]]
                                        for c in outp["chunks"]],
                            "vector_dim": len(vecs[0]) if vecs else None,
                            "vectors_finite": finite,
                            "l2_norms_minmax": [
                                round(min((sum(x * x for x in v) ** 0.5
                                           for v in vecs), default=0), 6),
                                round(max((sum(x * x for x in v) ** 0.5
                                           for v in vecs), default=0), 6)],
                        })
                        (raw / f"L{level}_{pdf.stem}.json").write_bytes(body)
                    else:
                        rec["sha_header_ok"] = sha_ok
                        rec["error"] = (f"completion_proof_missing: "
                                        f"status={resp.status_code} sha_ok={sha_ok}")
                        rec["error_raw"] = body[:400].decode(errors="replace")
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
                        print(f"[lg L{level}] {state['n']}/{len(docs)}", flush=True)

        await asyncio.gather(*[one(p) for p in docs])
        span = (time.perf_counter_ns() - t_level0) / 1e9
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    meta = {"kind": "level_meta", "arm": "langgraph", "level": level,
            "n_docs": len(docs), "level_span_s": span,
            "client_cpu_s": round((cpu1.ru_utime - cpu0.ru_utime)
                                  + (cpu1.ru_stime - cpu0.ru_stime), 2),
            "client_maxrss_mb": round(cpu1.ru_maxrss / 1048576, 1)}
    fh.write(json.dumps(meta) + "\n")
    fh.close()
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
