"""RocketRide batch driver — 200-doc run against the ALREADY-RUNNING engine.

Extends send_one.py: one connection, one launched pipe (fresh uuid4), then
sequential send_files per document. Does NOT boot an engine and does NOT
spawn per-doc pipes. Fail-soft: per-doc errors are recorded and the run
continues; on a dead connection it reconnects and relaunches once.

Run inside the container:
  docker exec prodbench-rocketride python /work/rr_batch.py /work/corpus /work/results_rr
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
PER_DOC_TIMEOUT_S = 180

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
        stamped = Path(f"/tmp/batch_{pipe['project_id']}.pipe")
        stamped.write_text(json.dumps(pipe))
        used = await self.client.use(filepath=str(stamped))
        self.token = used["token"]
        stamped.unlink(missing_ok=True)
        print(f"[rr] pipe launched token={self.token}", flush=True)

    async def down(self):
        try:
            if self.token:
                await self.client.terminate(self.token)
        except Exception:
            pass
        try:
            await self.client.disconnect()
        except Exception:
            pass

    async def send(self, pdf: Path):
        return await asyncio.wait_for(
            self.client.send_files([(str(pdf), {"doc_id": pdf.stem})], self.token),
            timeout=PER_DOC_TIMEOUT_S,
        )


async def main(corpus_dir: str, out_dir: str) -> int:
    corpus = sorted(Path(corpus_dir).glob("*.pdf"))
    out = Path(out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    per_doc = open(out / "per_doc_rr.jsonl", "a")
    done = set()
    prior = out / "per_doc_rr.jsonl"
    if prior.exists():
        for line in prior.read_text().splitlines():
            try:
                done.add(json.loads(line)["doc"])
            except Exception:
                pass
    print(f"[rr] corpus={len(corpus)} docs, {len(done)} already done", flush=True)

    arm = Arm()
    await arm.up()
    relaunches = 0
    t_run = time.time()

    async def attempt(pdf: Path, rec: dict) -> bool:
        """One send attempt; fills rec; returns True on success."""
        t0 = time.perf_counter()
        try:
            up = await arm.send(pdf)
            docs = documents_from(up)
            texts = [d.get("page_content", "") for d in docs]
            vecs = [d.get("embedding") or [] for d in docs]
            rec.update({
                "ok": len(docs) > 0,
                "n_chunks": len(docs),
                "chunk_lens": [len(t) for t in texts],
                "total_chars": sum(len(t) for t in texts),
                "vector_dim": len(vecs[0]) if vecs and vecs[0] else None,
                "l2_norms": [round(sum(x * x for x in v) ** 0.5, 8) for v in vecs],
                "model_id": docs[0].get(MODEL_KEY) if docs else None,
                "chunk_sha256": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
                "wall_s_emulated_not_reportable": round(time.perf_counter() - t0, 3),
            })
            if not docs:
                rec["error"] = "no documents returned"
                return False
            rec.pop("error", None)
            (out / "raw" / f"{pdf.stem}.json").write_text(
                json.dumps({"texts": texts, "vectors": vecs})
            )
            return True
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"[:400]
            rec["wall_s_emulated_not_reportable"] = round(time.perf_counter() - t0, 3)
            return False

    async def relaunch(reason: str):
        # A failed doc can wedge the engine pipe while the WebSocket stays
        # connected, so is_connected() is NOT a valid health signal — always
        # tear down and relaunch a fresh pipe (fresh uuid4).
        #
        # terminate() does NOT reliably reap a wedged pipe's backend process:
        # observed a backend surviving with 2.6 GB RSS (4x normal), starving
        # every later pipe under the container memory limit. Sends are
        # sequential and the new pipe does not exist yet, so killing ALL
        # node.py backends here is safe and guarantees a clean slate.
        nonlocal relaunches
        relaunches += 1
        print(f"[rr] relaunching pipe (#{relaunches}) after: {reason}", flush=True)
        await arm.down()
        os.system("pkill -9 -f 'ai/node.py' 2>/dev/null")
        await asyncio.sleep(2)
        await arm.up()

    for i, pdf in enumerate(corpus):
        if pdf.name in done:
            continue
        rec = {"doc": pdf.name, "arm": "rocketride", "ok": False}
        if not await attempt(pdf, rec):
            first_error = rec.get("error", "unknown")
            # Fresh pipe, one retry: separates "doc is bad" from "pipe was
            # already wedged by an earlier doc".
            await relaunch(f"{pdf.name}: {first_error}")
            rec["first_attempt_error"] = first_error
            if not await attempt(pdf, rec):
                rec["wedges_pipe"] = True  # failed even on a fresh pipe
                await relaunch(f"{pdf.name} failed again on fresh pipe")
        per_doc.write(json.dumps(rec) + "\n")
        per_doc.flush()
        if (i + 1) % 20 == 0:
            print(f"[rr] {i+1}/{len(corpus)} elapsed={time.time()-t_run:.0f}s "
                  f"relaunches={relaunches}", flush=True)
    await arm.down()
    per_doc.close()
    print(f"[rr] DONE in {time.time()-t_run:.0f}s (emulated, not reportable)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
