"""RocketRide VIDEO batch driver — sizing runs (adapted from aws_bench/bench/rr_driver.py).

Same record schema and response-unwrapping as the PDF driver; differences:
  - corpus is *.avi; per-doc audio duration joined from corpus_manifest.json,
    so throughput can be quoted as MEETING-MINUTES PER SECOND (x realtime),
    the unit that answers "how long will N hours of footage take".
  - default mode is blast (ONE send_files batch — RocketRide's native path).
  - this is a SIZING run, not a gated benchmark: no envelope, single rep,
    the R5 whisper nondeterminism finding stands and is irrelevant here.

  python3 bench_video.py <corpus_dir> <out_dir> <n_docs> [mode] [warm_docs]
"""

import asyncio
import collections
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

URI = os.environ.get("ROCKETRIDE_URI", "ws://rocketride:5565/task/service")
APIKEY = os.environ.get("ROCKETRIDE_APIKEY", "local-dev")
PIPE_SRC = Path(os.environ.get("BENCH_PIPE", "/pipe/benchmark_video.pipe"))
TIMEOUT_S = int(os.environ.get("BENCH_TIMEOUT_S", "21600"))
EMBED_DIM = 384
POOL_MAX = int(os.environ.get("RR_POOL_MAX", "0")) or 10 ** 9
ARM = "rocketride-docker-3.3.1-video"
VIDEO_DURS: dict = {}


def documents_from(result):
    """Unwrap the engine's nested response shapes (same as rr_driver.py)."""
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


def base_record(video, durations):
    return {"doc": video.name, "arm": ARM, "ok": False,
            "input_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "size_bytes": video.stat().st_size,
            "duration_s": durations.get(video.name),
            "video_duration_s": VIDEO_DURS.get(video.name)}


def embedding_digest(docs):
    """sha256 over the ordered embedding sequence (haystack-suite digest) —
    lets determinism compare vectors across runs/modes without storing them."""
    import struct
    h = hashlib.sha256()
    for d in docs:
        v = d.get("embedding") or []
        h.update(struct.pack(f"<{len(v)}d", *v))
    return h.hexdigest()


def fill_from_docs(rec, docs):
    okv, why = verify(docs)
    texts = [d.get("page_content", "") for d in docs]
    rec["n_chunks"] = len(docs)
    rec["total_chars"] = sum(len(t) for t in texts)
    # Frame/detection counts for the V0 gates (frame_law, detection_ratio).
    # Concatenating chunks reassembles the full text exactly (no overlap,
    # verified), though boundary newlines are dropped by the splitter — so
    # frames are counted by their array-start patterns, which concatenation
    # restores even when a split fell mid-line or between '[' and '{'.
    full = "".join(texts)
    rec["n_frames_est"] = full.count("[{") + full.count("[]")
    rec["n_detections"] = full.count('"label"')
    rec["chunk_sha256"] = [hashlib.sha256(t.encode("utf-8")).hexdigest()
                           for t in texts]
    rec["vector_dim"] = EMBED_DIM if okv else None
    norms = [sum(x * x for x in (d.get("embedding") or [])) ** 0.5 for d in docs]
    rec["l2_norms_minmax"] = ([round(min(norms), 6), round(max(norms), 6)]
                              if norms else None)
    rec["embedding_sha256"] = embedding_digest(docs) if docs else None
    return okv, why


def records_from_batch(corpus, out, t0_ns, event_times, durations):
    """Per-doc records from ONE batched send_files() response — attribution by
    filepath basename, derived timings marked as derived (see rr_driver.py)."""
    items = out if isinstance(out, list) else [out]
    by_name = {}
    for it in items:
        if isinstance(it, dict):
            fp = it.get("filepath")
            if isinstance(fp, str) and fp:
                by_name.setdefault(Path(fp).name, it)

    recs = []
    for video in corpus:
        it = by_name.get(video.name)
        rec = base_record(video, durations)
        rec["submit_ns"] = t0_ns
        rec["timing_source"] = "batch_upload_time (derived, not measured)"
        if it is None:
            rec["completion_ns"] = t0_ns
            rec["identity_ok"] = False
            rec["reason"] = "no_response_for_file"
            recs.append(rec)
            continue
        ut = it.get("upload_time")
        ev = event_times.get(video.name)
        if ev is not None:
            rec["completion_ns"] = ev
            rec["timing_source"] = "client-observed upload event"
        elif isinstance(ut, (int, float)):
            rec["completion_ns"] = t0_ns + int(float(ut) * 1e9)
        else:
            rec["completion_ns"] = t0_ns
        rec["upload_time_s"] = ut
        rec["identity_ok"] = True
        docs = documents_from(it)
        okv, why = fill_from_docs(rec, docs)
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
    mode = sys.argv[4] if len(sys.argv) > 4 else "blast"
    warm_docs = int(sys.argv[5]) if len(sys.argv) > 5 else 0

    all_videos = sorted(corpus_dir.glob("*.avi"))
    corpus = all_videos[:n]
    if not corpus:
        raise SystemExit(f"no videos in {corpus_dir}")
    out.mkdir(parents=True, exist_ok=True)

    durations, corpus_shas, video_durs = {}, {}, {}
    mf = corpus_dir / "corpus_manifest.json"
    if mf.exists():
        _m = json.loads(mf.read_text())
        durations = _m.get("duration_s", {})
        corpus_shas = _m.get("sha256", {})
        video_durs = _m.get("video_duration_s", {})
        VIDEO_DURS.update(video_durs)
    measured_audio_s = sum(durations.get(v.name) or 0 for v in corpus)

    (out / "manifest.json").write_text(json.dumps(
        {"docs": [v.name for v in corpus], "n": len(corpus),
         "measured_audio_s": measured_audio_s,
         # sha map from the corpus manifest -> the corpus_pin gate verifies
         "sha256": {v.name: corpus_shas[v.name]
                    for v in corpus if v.name in corpus_shas},
         "video_duration_s": {v.name: video_durs[v.name]
                              for v in corpus if v.name in video_durs}}))

    pipe = json.loads(PIPE_SRC.read_text())
    pipe["project_id"] = str(uuid.uuid4())
    pipe_path = out / "pipeline.pipe"
    pipe_path.write_text(json.dumps(pipe))

    print(f"[rrv] corpus={len(corpus)} docs, "
          f"{measured_audio_s/3600:.2f} h of footage, mode={mode}, "
          f"warm={warm_docs}, timeout={TIMEOUT_S}s", flush=True)

    event_times: dict = {}
    event_actions: collections.Counter = collections.Counter()
    # Live per-video progress, flushed line-by-line as terminal events arrive,
    # so a watcher (and the S3 sync loop) sees completions DURING a blast
    # instead of one blob at the end. Append-only; the post-run per_doc.jsonl
    # stays the authoritative record.
    progress_fh = open(out / "progress.jsonl", "a", buffering=1)
    run_t0 = time.perf_counter_ns()

    async def on_event(ev):
        try:
            if ev.get("event") != "apaevt_status_upload":
                return
            b = ev.get("body") or {}
            act = b.get("action")
            event_actions[act] += 1
            if act in ("complete", "error"):
                fp = b.get("filepath")
                if fp:
                    name = Path(fp).name
                    event_times.setdefault(name, time.perf_counter_ns())
                    done = len(event_times)
                    t_rel = (time.perf_counter_ns() - run_t0) / 1e9
                    progress_fh.write(json.dumps(
                        {"doc": name, "action": act,
                         "t_rel_s": round(t_rel, 1), "n_done": done}) + "\n")
                    print(f"[rrv] progress: {name} {act} "
                          f"({done} terminal events, t+{t_rel:.0f}s)", flush=True)
        except Exception:
            pass

    # RR_THREADS unset -> the engine's own default pool (what an untuned
    # deployment gets; every video run before 2026-08-20 ran this way).
    # Set -> that pool size is requested via use(threads=N). Same caveat as
    # the PDF bench: threads REQUESTED is not threads activated — the real
    # pool is not observable from the client; effective cores come from the
    # cgroup sampler.
    rr_threads = os.environ.get("RR_THREADS")
    threads = int(rr_threads) if rr_threads else None

    client = RocketRideClient(uri=URI, auth=APIKEY, on_event=on_event)
    await client.connect()
    use_kwargs = dict(filepath=str(pipe_path), use_existing=True, ttl=28800)
    if threads:
        use_kwargs["threads"] = threads
    used = await client.use(**use_kwargs)
    token = used["token"]
    print(f"[rrv] pipeline up, token={token}, threads_requested="
          f"{threads if threads else 'NONE (engine default)'}", flush=True)

    try:
        await client.set_events(token, ["apaevt_status_upload"])
    except Exception as exc:
        print(f"[rrv] set_events unavailable ({type(exc).__name__}); per-doc "
              f"latency falls back to derived", flush=True)

    # Warm-start with docs DISJOINT from the measured set (list tail).
    warm_set = all_videos[n:n + warm_docs]
    warm_s = None
    if warm_docs and len(warm_set) < warm_docs:
        print(f"[rrv] WARNING: only {len(warm_set)} warm docs available", flush=True)
    if warm_set:
        tw = time.perf_counter_ns()
        await asyncio.wait_for(
            client.send_files([(str(p), {"doc_id": f"warm-{p.stem}"})
                               for p in warm_set], token),
            timeout=TIMEOUT_S)
        warm_s = (time.perf_counter_ns() - tw) / 1e9
        event_times.clear(); event_actions.clear()
        print(f"[rrv] warm-start: {len(warm_set)} docs in {warm_s:.1f}s "
              f"(excluded from measurement)", flush=True)

    mono_offset_ns = time.time_ns() - time.perf_counter_ns()
    t0 = time.perf_counter_ns()

    if mode == "blast":
        payload = [(str(v), {"doc_id": v.stem}) for v in corpus]
        batch_result = await asyncio.wait_for(client.send_files(payload, token),
                                              timeout=TIMEOUT_S)
        span = (time.perf_counter_ns() - t0) / 1e9
        span_ns = time.perf_counter_ns() - t0
        usable = dict(event_times)
        if usable:
            last = max(usable.values()) - t0
            frac = last / span_ns if span_ns else 0
            if frac < 0.5:
                print(f"[rrv] upload events done at {frac:.0%} of span -> they "
                      f"signal UPLOAD, not processing; falling back to derived",
                      flush=True)
                usable = {}
            else:
                print(f"[rrv] client-observed completions for {len(usable)}/"
                      f"{len(corpus)} (last at {frac:.0%} of span)", flush=True)
        records = records_from_batch(corpus, batch_result, t0, usable, durations)
    else:
        if mode == "seq":
            offered = 1
        elif mode.startswith("c") and mode[1:].isdigit():
            offered = int(mode[1:])
        else:
            raise SystemExit(f"bad mode {mode!r}: expected blast | seq | c<N>")
        sem = asyncio.Semaphore(offered)
        done = [0]

        async def one(video):
            async with sem:
                rec = base_record(video, durations)
                rec["submit_ns"] = time.perf_counter_ns()
                try:
                    up = await asyncio.wait_for(
                        client.send_files([(str(video), {"doc_id": video.stem})],
                                          token),
                        timeout=TIMEOUT_S)
                    rec["completion_ns"] = time.perf_counter_ns()
                    items = [u for u in (up if isinstance(up, list) else [])
                             if isinstance(u, dict)
                             and u.get("filepath") == str(video)]
                    rec["identity_ok"] = bool(items)
                    docs = documents_from(items if items else up)
                    okv, why = fill_from_docs(rec, docs)
                    rec["ok"] = bool(okv and items)
                    rec["reason"] = ("completed" if rec["ok"] else
                                     "no_documents" if not docs else
                                     "completion_proof_missing")
                except asyncio.TimeoutError:
                    rec["completion_ns"] = time.perf_counter_ns()
                    rec["reason"] = "timeout"
                except Exception as exc:
                    rec["completion_ns"] = time.perf_counter_ns()
                    rec["reason"] = "transport_error"
                    rec["error"] = f"{type(exc).__name__}: {exc}"[:250]
                done[0] += 1
                print(f"[rrv] {done[0]}/{len(corpus)} {video.name} "
                      f"{rec['reason']}", flush=True)
                return rec

        records = list(await asyncio.gather(*[one(v) for v in corpus]))
        span = (time.perf_counter_ns() - t0) / 1e9

    ok_n = sum(1 for r in records if r.get("ok"))
    chunks = sum(r.get("n_chunks") or 0 for r in records)
    with open(out / "per_doc.jsonl", "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        fh.write(json.dumps({
            "kind": "shot_meta", "arm": ARM, "mode": mode,
            "n_docs": len(corpus), "ok_docs": ok_n,
            "span_s": round(span, 2),
            "measured_audio_s": measured_audio_s,
            "realtime_factor": round(measured_audio_s / span, 2) if span else None,
            "total_chunks": chunks,
            "timeout_s": TIMEOUT_S,
            "offered_concurrency": (len(corpus) if mode == "blast" else
                                    1 if mode == "seq" else int(mode[1:])),
            "pipe_sha256": hashlib.sha256(PIPE_SRC.read_bytes()).hexdigest(),
            "provenance": {"pipeline_kind": "detect", "interval_s": 15,
                           "detect_model": "rfdetr", "threshold": 0.3,
                           "embed_model": "multi-qa-MiniLM-L6-cos-v1",
                           "split": {"chunk_size": 4000, "overlap": 0},
                           "expect_dim": 384},
            "threads_requested": threads,
            "warm_docs": len(warm_set), "warm_s": warm_s,
            "event_actions_seen": dict(event_actions),
            "mono_offset_ns": mono_offset_ns,
            "envelope": "NONE — sizing run: no cpuset, engine threads unpinned",
        }) + "\n")

    print(f"[rrv] DONE mode={mode}: {ok_n}/{len(records)} ok, "
          f"{chunks} chunks, span={span:.1f}s "
          f"({measured_audio_s/3600:.2f} h footage -> "
          f"{measured_audio_s/span:.1f}x realtime)", flush=True)

    try:
        await client.terminate(token)
    except Exception as exc:
        print(f"[rrv] terminate failed: {type(exc).__name__}: {exc}", flush=True)
    try:
        await client.disconnect()
    except Exception:
        pass
    if ok_n < len(records):
        raise SystemExit(f"RUN INCOMPLETE: {len(records) - ok_n} docs not ok")


if __name__ == "__main__":
    asyncio.run(main())
