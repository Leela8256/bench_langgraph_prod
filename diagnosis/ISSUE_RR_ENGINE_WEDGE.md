# [Engine] Backend livelocks under concurrent PDF parse — all pipeline traffic silently stalls, no error surfaced, terminate() does not recover

**Severity:** High (silent total loss of service on the affected pipeline;
memory growth toward OOM; recovery requires SIGKILL of the backend process)
**Component:** engine backend / `parse` node (embedded Tika JVM)
**Version:** server v3.2.1, linux-x64 (`engine` binary sha256 `cf1fbf9ce72d…371316`), SDK `rocketride==1.3.0`
**Reporter:** Leela Dammalapati (benchmark harness findings, 2026-08-07 → 08-10)

---

## ⚠️ Attribution caveat — read first

Every observation below was made running the **linux-x64 build under x86
emulation on Apple Silicon** (Docker Desktop; no linux-arm64 build exists).
The failure signature is consistent BOTH with a product deadlock AND with
known emulation pathology around contended PI-futexes and `fork()` in
heavily-threaded translated processes. **First action for triage: run the
attached 15-minute repro on native Linux x64.** If it reproduces there,
this is a product bug and the forensics below apply directly; if not, close
as environment-specific and consider the linux-arm64 build request
(filed separately) the real fix.

Two sub-findings are believed attribution-safe regardless (see "Related
issues"): the deterministic silent-empty responses and the
teardown/zombie-reaping behavior.

## Summary

Under concurrent PDF traffic through a default-configured document pipeline
(`webhook → parse → preprocessor_langchain → embedding_transformer →
response_documents`, no `threads=` override, single shared instance via
`use_existing`), the backend process (`ai/node.py` host) enters a permanent
livelock:

- **all in-flight and subsequent documents stall forever** — no responses,
  no error frames, WebSocket sessions stay healthy;
- the backend **burns ~all available cores indefinitely** while completing
  nothing (measured ~18 cores for 680 s, zero output);
- **RSS grows unboundedly** (observed 2.6 → 8.3 GB against a 10 GB cgroup
  cap as stalled work accumulates);
- `terminate()` on the token does **not** clear the state; `--autoterm`
  never fires; only `SIGKILL` of the backend recovers the engine;
- onset is **nondeterministic**: identical configurations wedged after 18,
  23, and 175 documents on different runs — and once ran 50/50 clean.
  Probability appears to scale with concurrent parse operations (offered
  load of 500 wedged at 0 completions, twice consecutively, with verified
  successful warmups in between).

## Stage isolation — the parse/Tika path is the trigger

Discriminating experiment, identical clean-restart procedure, same engine,
same 8-connection pool, same concurrency:

| corpus | path exercised | result |
|---|---|---|
| 100 Govdocs PDFs @ 8 in-flight | parse (Tika JVM) + splitter + embedder | **wedged at 18 docs** |
| the same 100 documents' extracted text (.txt) @ 8 in-flight | splitter + embedder (parse bypassed by MIME routing) | **100/100 clean, 36 s, zero failures** |

The splitter and embedder sustain concurrency 8 indefinitely. Only runs
that exercise `parse` wedge. (This also explains why internal chat/LLM
benchmarks never hit it — they don't run `parse`.)

## Forensics captured at a live wedge

Process: single backend hosts the JVM (G1 GC + compiler threads observed),
Python, and torch in one address space. At freeze:

- **167 of 205 threads blocked in `rt_mutex_schedule`** (kernel
  priority-inheritance mutex wait); 38 more in `futex_wait_queue`.
- **`jspawnhelper` child alive and stuck mid-handshake**
  (`…/java/jre/lib/jspawnhelper 17.0.19+10 <fd:fd:fd>`), plus defunct
  `jspawnhelper` zombies never reaped. Present at every captured wedge.
- Backend at 3.0 GB RSS and climbing, 199 open fds.
- `docker logs`: nothing but healthy WebSocket accepts. No engine-side
  error, warning, or timeout for the stalled documents — the failure is
  invisible except as client-side silence.

## Spawn-source hypothesis (for the engine team to confirm)

The shipped `tika-config.xml` excludes `TesseractOCRParser`, and no
`tesseract`/`magick` binary exists in the image — yet `jspawnhelper`
activity proves the JVM attempts process spawns during PDF parsing. The
`tika-parser-ocr-module-3.2.3.jar` and `commons-exec` are on the
classpath, and Tika's Tesseract integration probes binary availability by
exec'ing `tesseract`. Suspicion: **repeated availability-probe execs for a
binary that does not exist**, i.e. the entire fragile spawn path may be
pure overhead. If confirmed, removing the OCR module from the classpath
(or hard-disabling OCR strategy) eliminates the spawn traffic wholesale.

## Reproduction package

All artifacts in the benchmark repo (`prod bench`, git `60c5479`):

- `diagnosis/DIAGNOSIS.md` — full analysis
- `diagnosis/wedge_capture.sh` — self-contained repro: clean restart →
  8 warm pooled clients → 100 PDFs at 8 in-flight → on 90 s stall,
  captures ps, per-thread states, wchan histogram, spawn-helper state
- `runs/diagnosis/pdf-c8/wedge_forensics.txt` — the capture quoted above
- `runs/pdf500/rr/` — 500-doc open-loop shot (0/500, two wedges, with
  per-wedge diagnostics and 100 ms process-tree samplers)
- Corpus: first N valid PDFs of public Govdocs1 (deterministic recipe in
  `results500/manifest.jsonl`) — no proprietary data involved

Repro rate in our environment: 4 of 5 concurrent-PDF runs wedged
(the 5th completed 44/50 — nondeterminism is part of the signature).

## Impact assessment

Any production document-ingestion pipeline using `parse` with default
settings and concurrent traffic can silently freeze: clients hang forever
(the SDK's `send_files` intentionally applies no cap and no timeout,
"let server handle queuing"), the engine reports healthy, memory climbs
toward the container/host limit, and operators get no signal short of
external progress monitoring. Recovery requires killing the backend
process; `terminate()` is insufficient.

## Suggested fixes, ranked

1. Confirm/eliminate the spurious spawn path (OCR availability probes) —
   possibly a one-line classpath or config change.
2. Try `-Djdk.lang.Process.launchMechanism=vfork|fork` on the embedded
   JVM — documented workaround family for stuck `jspawnhelper`.
3. Bound parse concurrency internally (semaphore on the Tika bridge);
   threadCount 64 is advertised but ≥8 concurrent parses can wedge.
4. Process-isolate the JVM (tika-server sidecar / forked parser pool):
   makes deadlocks containable and killable, isolates memory from
   torch/Python.
5. Engine-side progress watchdog: recycle a zero-progress pipeline AND
   emit an error frame to clients — converts silent infinite hangs into
   bounded, visible failures even before root cause is fixed.
6. Fix teardown: reap zombie spawn helpers; make `terminate()` escalate
   to SIGKILL on an unresponsive backend; make `--autoterm` fire.

## Related issues to file separately

1. **Deterministic silent-empty parse results** — `000164.pdf` and
   `000357.pdf` (Govdocs1) reproducibly return a success-shaped response
   with an empty `documents` list and no error, at concurrency 1, across
   engine sessions. Likely a genuine parse defect independent of the wedge
   (clean responses, not stress behavior).
2. **`terminate()` does not reap wedged backends** — observed a terminated
   pipe's backend surviving at 2.6 GB RSS, starving later pipelines under
   the memory cap (also folded into fix #6 above).
3. **WebSocket upgrade rejected through Docker published-port proxy** —
   `ws://host:5565/task/service` fails with "did not receive a valid HTTP
   response" via docker-proxy while working container-internally; the
   published port is effectively unusable for the SDK.
4. **Feature request: linux-arm64 build** — no ARM Linux build exists
   across all published releases; Apple-Silicon and ARM-cloud users must
   emulate, which both distorts performance and (pending the attribution
   test) may itself induce the wedge.
