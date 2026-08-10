"""RocketRide BULK batch driver — parallel upload in batches of 10.

send_files() natively uploads a file list concurrently, so each batch is one
SDK call. Recovery model, learned the hard way on the sequential run:
  - a bad doc can wedge the pipe while the WebSocket stays healthy;
  - terminate() does not reap a wedged backend (observed 2.6 GB RSS zombie
    starving all later pipes under the 4 GB container limit).
So: on any batch failure, reap ALL node.py backends, relaunch a fresh pipe
(fresh uuid4), and re-run that batch's docs INDIVIDUALLY so bad documents are
identified per-doc, each with one fresh-pipe retry.

Run inside the container:
  docker exec prodbench-rocketride python /work/rr_bulk.py /work/corpus /work/results_rr
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
BATCH_SIZE = 10
BATCH_TIMEOUT_S = 420
DOC_TIMEOUT_S = 180
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


def record_from(pdf_name: str, docs, wall_s: float, mode: str) -> dict:
    texts = [d.get("page_content", "") for d in docs]
    vecs = [d.get("embedding") or [] for d in docs]
    rec = {
        "doc": pdf_name,
        "arm": "rocketride",
        "mode": mode,
        "ok": len(docs) > 0,
        "n_chunks": len(docs),
        "chunk_lens": [len(t) for t in texts],
        "total_chars": sum(len(t) for t in texts),
        "vector_dim": len(vecs[0]) if vecs and vecs[0] else None,
        "l2_norms": [round(sum(x * x for x in v) ** 0.5, 8) for v in vecs],
        "model_id": docs[0].get(MODEL_KEY) if docs else None,
        "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
        "wall_s_emulated_not_reportable": round(wall_s, 3),
    }
    if not docs:
        rec["error"] = "no documents returned"
    return rec


class Arm:
    def __init__(self):
        self.client = None
        self.token = None

    async def up(self):
        from rocketride import RocketRideClient

        self.client = RocketRideClient(uri=URI, auth=APIKEY)
        await self.client.connect()
        pipe = json.loads(PIPE_SRC.read_text())
        pipe["project_id"] = str(uuid.uuid4())
        stamped = Path(f"/tmp/bulk_{pipe['project_id']}.pipe")
        stamped.write_text(json.dumps(pipe))
        used = await self.client.use(filepath=str(stamped))
        self.token = used["token"]
        stamped.unlink(missing_ok=True)
        print(f"[rr-bulk] pipe launched token={self.token}", flush=True)

    async def down_and_reap(self):
        try:
            if self.token:
                await self.client.terminate(self.token)
        except Exception:
            pass
        try:
            await self.client.disconnect()
        except Exception:
            pass
        os.system("pkill -9 -f 'ai/node.py' 2>/dev/null")
        await asyncio.sleep(2)


async def main(corpus_dir: str, out_dir: str) -> int:
    corpus = sorted(Path(corpus_dir).glob("*.pdf"))
    out = Path(out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    path = out / "per_doc_rr.jsonl"
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                done.add(json.loads(line)["doc"])
            except Exception:
                pass
    todo = [p for p in corpus if p.name not in done]
    per_doc = open(path, "a")
    print(f"[rr-bulk] corpus={len(corpus)} done={len(done)} todo={len(todo)}", flush=True)

    arm = Arm()
    await arm.up()
    relaunches = 0
    t_run = time.time()

    def write(rec: dict):
        per_doc.write(json.dumps(rec) + "\n")
        per_doc.flush()

    def save_raw(stem: str, docs):
        texts = [d.get("page_content", "") for d in docs]
        vecs = [d.get("embedding") or [] for d in docs]
        (out / "raw" / f"{stem}.json").write_text(
            json.dumps({"texts": texts, "vectors": vecs})
        )

    async def relaunch(reason: str):
        nonlocal relaunches
        relaunches += 1
        print(f"[rr-bulk] relaunch #{relaunches}: {reason}", flush=True)
        await arm.down_and_reap()
        await arm.up()

    async def run_doc_individually(pdf: Path):
        """Per-doc with one fresh-pipe retry — the sequential recovery path."""
        for attempt in (1, 2):
            t0 = time.perf_counter()
            try:
                up = await asyncio.wait_for(
                    arm.client.send_files([(str(pdf), {"doc_id": pdf.stem})], arm.token),
                    timeout=DOC_TIMEOUT_S,
                )
                docs = documents_from(up)
                rec = record_from(pdf.name, docs, time.perf_counter() - t0,
                                  "sequential-fallback")
                if docs:
                    save_raw(pdf.stem, docs)
                    write(rec)
                    return
                raise RuntimeError("no documents returned")
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"[:300]
                if attempt == 1:
                    await relaunch(f"{pdf.name}: {err}")
                else:
                    write({"doc": pdf.name, "arm": "rocketride",
                           "mode": "sequential-fallback", "ok": False,
                           "error": err, "wedges_pipe": True,
                           "wall_s_emulated_not_reportable":
                               round(time.perf_counter() - t0, 3)})
                    await relaunch(f"{pdf.name} failed on fresh pipe")

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        t0 = time.perf_counter()
        try:
            up = await asyncio.wait_for(
                arm.client.send_files(
                    [(str(p), {"doc_id": p.stem}) for p in batch], arm.token
                ),
                timeout=BATCH_TIMEOUT_S,
            )
            wall = time.perf_counter() - t0
            # map per-file results back by filepath
            by_path = {}
            if isinstance(up, list):
                for item in up:
                    if isinstance(item, dict) and item.get("filepath"):
                        by_path[Path(item["filepath"]).name] = documents_from(item)
            missing = [p for p in batch if not by_path.get(p.name)]
            for p in batch:
                docs = by_path.get(p.name) or []
                if docs:
                    save_raw(p.stem, docs)
                    write(record_from(p.name, docs, wall / len(batch), "bulk"))
            if missing:
                print(f"[rr-bulk] {len(missing)} docs empty in batch -> "
                      f"individual retry: {[p.name for p in missing]}", flush=True)
                await relaunch("empty results in batch")
                for p in missing:
                    await run_doc_individually(p)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:200]
            print(f"[rr-bulk] batch failed ({err}) -> per-doc fallback for "
                  f"{[p.name for p in batch]}", flush=True)
            await relaunch(f"batch: {err}")
            for p in batch:
                await run_doc_individually(p)
        n_done = len(done) + min(start + BATCH_SIZE, len(todo))
        print(f"[rr-bulk] ~{n_done}/{len(corpus)} elapsed={time.time()-t_run:.0f}s "
              f"relaunches={relaunches}", flush=True)

    await arm.down_and_reap()
    per_doc.close()
    print(f"[rr-bulk] DONE in {time.time()-t_run:.0f}s (emulated, not reportable)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
