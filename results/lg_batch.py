"""LangGraph batch driver — 200-doc run against the running container on :8100.

Sequential HTTP posts; verifies X-Output-SHA256 against the body per request.
Fail-soft, resumable, checkpointed per doc.

Run with the LangGraph venv python (has httpx):
  langgraph-fastapi/.venv/bin/python results/lg_batch.py datasets/govdocs results
"""

import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8100"
PIPELINE = "document-pdf-v1"
PER_DOC_TIMEOUT_S = 300


def main(corpus_dir: str, out_dir: str) -> int:
    corpus = sorted(Path(corpus_dir).glob("*.pdf"))
    out = Path(out_dir)
    (out / "raw_lg").mkdir(parents=True, exist_ok=True)
    path = out / "per_doc_lg.jsonl"
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                done.add(json.loads(line)["doc"])
            except Exception:
                pass
    per_doc = open(path, "a")
    print(f"[lg] corpus={len(corpus)} docs, {len(done)} already done", flush=True)

    t_run = time.time()
    with httpx.Client(base_url=BASE, timeout=PER_DOC_TIMEOUT_S) as client:
        assert client.get("/health/ready").status_code == 200, "server not ready"
        for i, pdf in enumerate(corpus):
            if pdf.name in done:
                continue
            rec = {"doc": pdf.name, "arm": "langgraph", "ok": False}
            t0 = time.perf_counter()
            try:
                resp = client.post(
                    f"/v1/process/{PIPELINE}",
                    files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
                    data={"request_id": f"bench-{pdf.stem}"},
                )
                rec["http_status"] = resp.status_code
                body = resp.content
                claimed = resp.headers.get("x-output-sha256")
                rec["sha_header_ok"] = (
                    claimed == hashlib.sha256(body).hexdigest() if claimed else None
                )
                if resp.status_code == 200:
                    output = json.loads(body)["output"]
                    texts = [c["text"] for c in output["chunks"]]
                    vecs = output["vectors"]
                    rec.update({
                        "ok": True,
                        "n_chunks": len(texts),
                        "chunk_lens": [len(t) for t in texts],
                        "total_chars": sum(len(t) for t in texts),
                        "vector_dim": len(vecs[0]) if vecs else None,
                        "l2_norms": [
                            round(sum(x * x for x in v) ** 0.5, 8) for v in vecs
                        ],
                        "model_id": "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
                        "chunk_sha256": [
                            hashlib.sha256(t.encode()).hexdigest() for t in texts
                        ],
                        "offsets": [[c["start"], c["end"]] for c in output["chunks"]],
                        "offsets_sha256": hashlib.sha256(
                            json.dumps(
                                [[c["start"], c["end"]] for c in output["chunks"]]
                            ).encode()
                        ).hexdigest(),
                    })
                    (out / "raw_lg" / f"{pdf.stem}.json").write_text(
                        json.dumps({"texts": texts, "vectors": vecs})
                    )
                else:
                    try:
                        rec["error"] = json.loads(body)["error"]["code"]
                    except Exception:
                        rec["error"] = body[:200].decode(errors="replace")
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"[:400]
            rec["wall_s_emulated_not_reportable"] = round(time.perf_counter() - t0, 3)
            per_doc.write(json.dumps(rec) + "\n")
            per_doc.flush()
            if (i + 1) % 20 == 0:
                print(f"[lg] {i+1}/{len(corpus)} elapsed={time.time()-t_run:.0f}s", flush=True)
    per_doc.close()
    print(f"[lg] DONE in {time.time()-t_run:.0f}s (emulated, not reportable)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
