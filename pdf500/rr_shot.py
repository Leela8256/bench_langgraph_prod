"""PDF-500 RocketRide open-loop shot — in-container, pool of 8, wedge protocol.

Offered load: ALL docs fired at once (no client-side cap; the SDK itself has
none — verified in source). Completion = proof (parsed chunks+vectors +
filepath identity + persisted raw), never acknowledgement.

Preregistered wedge protocol:
  detection  : zero completions AND zero definitive failures for 300 s while
               work is pending (in-driver; host mtime watchdog is backstop)
  on wedge 1 : write diagnostics snapshot -> cancel pending -> pkill -9
               node.py backends -> relaunch pool (use_existing, same
               project_id) -> resubmit all unproven docs (attempt 2)
  on wedge 2 : stop the arm; every still-unproven doc recorded
               reason=wedge_affected; census stands as-is
Exactly one record per doc across attempts (cancelled attempts write
nothing; the final outcome writes once). Census: offered = records.

  docker exec prodbench-rocketride python /work/rr_shot.py \
      /work/corpus /work/out/pdf500_shot <timeout_s> <n_docs>
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/work")
URI = "ws://127.0.0.1:5565/task/service"
APIKEY = "local-dev"
STATE = ROOT / "pdf200" / "rr_pool_state.json"
WARMUP_DOC = ROOT / "data" / "probe" / "sample.pdf"
POOL_SIZE = 8
WEDGE_STALL_S = 300
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


class Shot:
    def __init__(self, corpus, out_dir, timeout_s, n_docs):
        self.docs = sorted(Path(corpus).glob("*.pdf"))[:n_docs]
        self.out = Path(out_dir)
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        self.fh = open(self.out / "per_doc.jsonl", "a")
        self.timeout_s = timeout_s
        self.pool = []
        self.lock = asyncio.Lock()
        self.last_progress = time.monotonic()
        self.written = set()
        self.n_done = 0
        self.wedge_events = []

    # ---------- pool ----------
    async def make_pool(self):
        from rocketride import RocketRideClient

        pipe_path = json.loads(STATE.read_text())["pipe_path"]
        pool = []
        for i in range(POOL_SIZE):
            c = RocketRideClient(uri=URI, auth=APIKEY)
            await c.connect()
            used = await c.use(filepath=pipe_path, use_existing=True, ttl=7200)
            pool.append({"client": c, "token": used["token"], "slot": i})
        self.pool = pool

    async def teardown_pool(self):
        for p in self.pool:
            try:
                await asyncio.wait_for(p["client"].disconnect(), timeout=10)
            except Exception:
                pass
        self.pool = []

    async def warmup(self):
        for p in self.pool:
            up = await asyncio.wait_for(
                p["client"].send_files(
                    [(str(WARMUP_DOC), {"doc_id": f"warm-{p['slot']}"})],
                    p["token"]), timeout=120)
            ok, why = verify_docs(documents_from(up))
            if not ok:
                raise SystemExit(f"WARMUP FAILED slot {p['slot']}: {why}")
        print(f"[shot] warmup ok: {len(self.pool)} slots", flush=True)

    # ---------- recording ----------
    def write(self, rec):
        assert rec["doc"] not in self.written, f"double write {rec['doc']}"
        self.written.add(rec["doc"])
        self.fh.write(json.dumps(rec) + "\n")
        self.fh.flush()
        self.n_done += 1
        self.last_progress = time.monotonic()
        if self.n_done % 25 == 0:
            os.fsync(self.fh.fileno())
            print(f"[shot] {self.n_done}/{len(self.docs)} recorded", flush=True)

    def diag_snapshot(self, tag):
        try:
            ps = subprocess.run(
                ["sh", "-c", "ps ax -o pid,rss,nlwp,args | head -30; free -m | head -2"],
                capture_output=True, text=True, timeout=30).stdout
        except Exception as e:
            ps = f"diag failed: {e}"
        (self.out / f"{tag}_diag.txt").write_text(ps)

    # ---------- per-doc task ----------
    async def one(self, i, pdf, attempt):
        slot = self.pool[i % len(self.pool)]
        rec = {"doc": pdf.name, "arm": "rocketride", "attempt": attempt,
               "slot": slot["slot"], "ok": False,
               "input_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
        rec["submit_ns"] = time.perf_counter_ns()
        try:
            up = await asyncio.wait_for(
                slot["client"].send_files([(str(pdf), {"doc_id": pdf.stem})],
                                          slot["token"]),
                timeout=self.timeout_s)
            rec["completion_ns"] = rec["first_result_ns"] = time.perf_counter_ns()
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
            if rec["ok"]:
                (self.out / "raw" / f"{pdf.stem}.json").write_text(
                    json.dumps(up, default=str))
                rec["reason"] = "completed"
            elif not docs_r:
                rec["reason"] = "no_documents"
                rec["error_raw"] = json.dumps(up, default=str)[:500]
            else:
                rec["reason"] = "completion_proof_missing"
                rec["error"] = why or "identity"
            async with self.lock:
                self.write(rec)
        except asyncio.TimeoutError:
            rec["completion_ns"] = time.perf_counter_ns()
            rec["reason"] = "completion_proof_missing"
            rec["error"] = f"timeout {self.timeout_s}s"
            async with self.lock:
                self.write(rec)
        except asyncio.CancelledError:
            # wedge cancellation: no record now; doc returns to unproven pool
            raise
        except Exception as exc:
            rec["completion_ns"] = time.perf_counter_ns()
            rec["reason"] = "transport_error"
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
            async with self.lock:
                self.write(rec)

    # ---------- one open-loop attempt over a doc set ----------
    async def attempt(self, docs, attempt_no):
        t0 = time.perf_counter_ns()
        tasks = {p.name: asyncio.create_task(self.one(i, p, attempt_no))
                 for i, p in enumerate(docs)}
        send_window = (time.perf_counter_ns() - t0) / 1e9
        self.last_progress = time.monotonic()
        wedged = False
        while any(not t.done() for t in tasks.values()):
            await asyncio.sleep(10)
            stalled = time.monotonic() - self.last_progress
            if stalled >= WEDGE_STALL_S:
                wedged = True
                pending = [n for n, t in tasks.items() if not t.done()]
                ev = {"kind": "wedge_event", "n": len(self.wedge_events) + 1,
                      "attempt": attempt_no, "at_records": self.n_done,
                      "pending": len(pending), "stalled_s": round(stalled, 1),
                      "ts": time.time()}
                self.wedge_events.append(ev)
                self.fh.write(json.dumps(ev) + "\n")
                self.fh.flush()
                os.fsync(self.fh.fileno())
                print(f"[shot] WEDGE #{ev['n']}: {len(pending)} pending, "
                      f"{self.n_done} recorded", flush=True)
                self.diag_snapshot(f"wedge{ev['n']}")
                for t in tasks.values():
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
                break
        if not wedged:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        unproven = [p for p in docs if p.name not in self.written]
        return wedged, unproven, send_window

    async def run(self):
        await self.make_pool()
        await self.warmup()
        t_shot0 = time.perf_counter_ns()
        wedged1, unproven, send_window = await self.attempt(self.docs, 1)
        relaunched = False
        if wedged1 and unproven:
            print(f"[shot] relaunching after wedge 1: reap backends, "
                  f"{len(unproven)} docs to resume", flush=True)
            await self.teardown_pool()
            os.system("pkill -9 -f 'ai/node.py' 2>/dev/null")
            await asyncio.sleep(3)
            await self.make_pool()
            await self.warmup()
            relaunched = True
            wedged2, unproven, _ = await self.attempt(unproven, 2)
            if wedged2:
                print(f"[shot] second wedge — stopping arm, "
                      f"{len(unproven)} wedge_affected", flush=True)
        # finalize anything still unproven as wedge_affected
        for p in unproven:
            self.write({"doc": p.name, "arm": "rocketride", "ok": False,
                        "reason": "wedge_affected",
                        "attempt": 2 if relaunched else 1,
                        "input_sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        span = (time.perf_counter_ns() - t_shot0) / 1e9
        meta = {"kind": "shot_meta", "arm": "rocketride",
                "n_docs": len(self.docs), "pool": POOL_SIZE,
                "send_window_s": send_window, "shot_span_s": round(span, 2),
                "wedge_events": self.wedge_events, "relaunched": relaunched,
                "timeout_s": self.timeout_s}
        self.fh.write(json.dumps(meta) + "\n")
        self.fh.close()
        await self.teardown_pool()
        print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    corpus, out_dir, timeout_s, n_docs = (
        sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
    asyncio.run(Shot(corpus, out_dir, timeout_s, n_docs).run())
