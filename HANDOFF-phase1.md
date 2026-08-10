# HANDOFF — LangGraph + FastAPI service, Phase 1 (Milestone 1: Contract)

Build ONLY Milestone 1. Do not build the document graph, models, executor,
Docker, or the driver. Do not add PostgreSQL, Redis, queues, or checkpointing.

## Scope of this phase

Deliver: schemas, canonical encoder, settings, error contract, pipeline
registry, FastAPI app with lifespan/warm-up/readiness, upload persistence,
and a full test suite running against a MOCK pipeline. Green pytest is the
exit criterion.

## Fixed decisions — implement exactly, do not redesign

1. **Endpoint**: `POST /v1/process/{pipeline}`. Phase 1 registers only
   `mock-v1`. Multipart/form-data: `file` (required UploadFile),
   `options` (optional JSON-object string, default `"{}"`),
   `request_id` (optional string; server generates uuid4 hex if absent).
2. **Supporting endpoints**: `GET /health/live` (process up),
   `GET /health/ready` (503 until every loaded pipeline's warm-up succeeded),
   `GET /meta` (effective config: pid, ready, pipelines, uvicorn_workers,
   executor_workers, max_inflight_requests, torch_threads,
   torch_interop_threads, applied env pins, architecture).
3. **Response body** (canonical bytes, via raw `Response`, never FastAPI's
   default serializer):
   `{"schema_version":1,"request_id":...,"pipeline":...,"input":{"filename","media_type","size_bytes"},"output":{...}}`
   Timings NEVER go in the body — headers only:
   `X-Request-ID`, `X-Pipeline`, `X-Output-SHA256`, `Server-Timing`.
4. **Canonical encoder**: one function, one module (`service/canonical.py`).
   ⚠ OPEN ITEM — flags must byte-match the encoder used by the existing
   offline reference in the benchmark repo (spec says
   `json.dumps(separators=(',',':'))`, UTF-8). Implement with flags as named
   module constants (`SEPARATORS`, `ENSURE_ASCII`, `SORT_KEYS`, `ALLOW_NAN`)
   defaulting to `separators=(",",":"), ensure_ascii=True, sort_keys=False,
   allow_nan=False`, plus a loud comment that these must be verified against
   the reference before any parity gate is trusted. Raise a dedicated
   exception on NaN/Inf/non-serializable — never emit them.
5. **Options are opaque pass-through.** The server defines NO chunking,
   model, or workload defaults. Any request-configurable chunking is
   forbidden — workload behavior gets pinned inside workload functions in
   Milestone 2 (splitter = RecursiveCharacterTextSplitter defaults 4000/200
   applied to text+'\n'; do NOT implement now, do NOT change later to 512 or
   2048).
6. **Pipeline abstraction**: registry maps name -> object satisfying
   `async ainvoke(state: dict) -> dict` (a compiled LangGraph graph satisfies
   this natively; that is the point). Optional `async warmup()`. Startup
   FAILS on: unknown configured pipeline, duplicate registration, warm-up
   failure. `PIPELINES` env var (comma-separated) selects what loads.
7. **State**: `MediaState` TypedDict — request_id, pipeline, source_path,
   filename, media_type, size_bytes, options, timings_ns, result. Large
   media rides as a temp-file path, never bytes-in-state.
8. **Upload**: stream to a temp file in `TEMP_DIRECTORY` in 1MB chunks,
   enforce `MAX_UPLOAD_BYTES` during the copy (fail at the limit, not
   after), empty file -> `empty_input`. Always cleanup in `finally`.
   Keep the temp-disk write — it is a deliberate production cost, do not
   optimize it away.
9. **Error contract** (stable JSON:
   `{"error":{"code","message","request_id"}}`), codes -> status:
   unknown_pipeline 404, server_not_ready 503, invalid_options 400,
   empty_input 400, unsupported_media_type 415, payload_too_large 413,
   processing_timeout 504, processing_failed 500,
   canonical_encoding_failed 500.
10. **Settings**: typed, from env, `/meta` reports effective values.
    Declare (even though machinery lands in Milestone 3): EXECUTOR_WORKERS
    (default 4), MAX_INFLIGHT_REQUESTS (8), REQUEST_TIMEOUT_SECONDS (300),
    TORCH_THREADS (1), TORCH_INTEROP_THREADS (1), MAX_UPLOAD_BYTES,
    TEMP_DIRECTORY, PIPELINES (default document-v1 — tests override to
    mock-v1), HOST, PORT (8100), LOG_LEVEL, BENCHMARK_MODE.
    `configure_runtime()` pins OMP/MKL/OPENBLAS_NUM_THREADS and
    TOKENIZERS_PARALLELISM=false via env at startup, before any heavy
    import, and records what it applied.
11. **One uvicorn worker** is the deployment shape
    (`uvicorn service.app:app --workers 1 --port 8100`). App factory
    pattern (`create_app(settings, builders)`) so tests inject both.
12. **Repo layout** (create exactly; empty dirs where noted):

    ```
    langgraph-fastapi/
      pyproject.toml
      service/  app.py config.py registry.py schemas.py canonical.py
                errors.py upload.py
      pipelines/  mock.py   (document/ image/ video/ shared/: empty stubs)
      workload/   (empty — Milestone 2; keep independent of LangGraph+FastAPI)
      client/     (empty — Milestone 4)
      tests/unit/ tests/integration/
      toil.md
    ```

## Mock pipeline (Phase 1 only)

`mock-v1`: reads the persisted temp file, returns
`{"kind":"mock","input_sha256":...,"size_bytes":...,"options_echo":...}` as
`state["result"]`, records `timings_ns["graph"]`. Deterministic on input
bytes so integration tests can assert exact canonical output bytes.

## Required tests (pytest; TestClient as context manager so lifespan runs)

Unit: canonical encoder (separators/no whitespace, determinism, unicode,
NaN/Inf/non-serializable rejected, sha256), settings parsing + invalid env,
registry (register/get/duplicate/unknown/sorted names), error bodies + codes.

Integration (PIPELINES=mock-v1): /health/live; /health/ready 503 before /
200 after warm-up; /meta shape; POST mock-v1 happy path — status 200, body
== independently computed canonical bytes, X-Output-SHA256 matches body
hash; unknown pipeline 404 with unknown_pipeline; malformed options 400;
non-object options 400; oversized upload 413; empty file 400; temp directory
empty after each request (cleanup verified); warm-up failure aborts startup.

## toil.md — start it now

First entries: multipart schema chosen and WHY (spooled uploads generalize
to video; server pays the temp-disk cost RocketRide pays); encoder flags
pending verification vs offline reference; every judgment call made where
this spec was silent.

## Open items (do not resolve silently — surface them)

- OPEN-1: canonical encoder flags vs the repo's offline-reference encoder.
- OPEN-2: multipart request shape must be ratified with Shashi (RocketRide
  arm) and Ansh (LlamaIndex arm) as THE shared cross-framework schema; the
  response envelope fields likewise.
- OPEN-3: this runs in a linux/amd64 container under Rosetta on an M5 Pro —
  all timings are shakeout only; nothing measured here is ever reported.

## Exit criteria

All tests green; server starts with PIPELINES=mock-v1; curl of one file
returns canonical bytes with correct headers; /meta truthful; toil.md has
its first entries. Then STOP and report back — Milestone 2 (document graph,
workload pinning, parity vs offline reference) is a separate handoff.
