# HANDOFF — Phase 1 (M1): Generic LangGraph Benchmark Server — Contract & Scaffolding

## What this is

The foundation of a benchmark comparing OSS LangGraph (deployed as a
production-style FastAPI service) against RocketRide. Phase 1 builds the
GENERIC server: transport, contracts, registry, and pipeline abstraction —
proven against a mock pipeline. No LangGraph, no models, no real
processing in this phase. The value of this phase is that every later
pipeline (document, PDF, image, video, agent graphs) drops in WITHOUT
changing the server, and every test written now stays valid verbatim.

## Later expansions (context only — DO NOT build any of it now)

- M2: document pipeline — real compiled LangGraph (extract -> normalize ->
  chunk -> embed -> assemble), workload functions, adapter.
- M3: parity — equality chain offline reference == workload == graph ==
  HTTP, resolving encoder flags against the mt10k reference.
- M4: runtime controls (bounded executor, admission semaphore, timeout).
- M5: benchmark driver (httpx pool, open/closed loop, 10k machinery run).
- M6: Docker (pinned image, one worker, fixed resources).
- M7: RocketRide comparison. M8: image/video/agent pipelines.

## Architecture principle (repeat back whenever scope drifts)

FastAPI owns transport. PipelineRegistry owns discovery. Pipeline adapters
translate requests into graph-specific state. OSS LangGraph (later) owns
orchestration. workload/ (later) owns computation. FastAPI must remain
unaware of graph topology so ANY compiled LangGraph — sequential,
branching, fan-out, loops, agent — can be added later without redesigning
the server.

## Hard prohibitions

- NO langgraph import anywhere in Phase 1. NO Agent Server, langgraph-cli,
  langgraph-sdk, LangSmith, Postgres, Redis, queues — ever, in any phase.
- NO modality-specific logic ("if pipeline == ...") anywhere in service/.
- Only service/canonical.py may serialize canonical response data.
- No models, no torch import, no network access at runtime.

## Repository layout (create exactly; empty dirs where noted)

```
langgraph-fastapi/
├── pyproject.toml
├── README.md
├── toil.md                    # started NOW, written while building
├── service/
│   ├── app.py config.py registry.py pipeline.py schemas.py
│   ├── canonical.py errors.py upload.py
├── pipelines/
│   └── mock.py                # document/ image/ video/ agent/: empty dirs
├── workload/                  # empty — M2
├── client/                    # empty — M5
└── tests/  unit/ integration/
```

## The generic pipeline contract (service/pipeline.py)

```python
class RequestContext:          # HTTP-side facts, NOT graph state
    request_id: str
    pipeline: str
    source_path: str | None
    filename: str | None
    media_type: str | None
    size_bytes: int | None
    options: dict

class Pipeline(Protocol):
    name: str
    async def warmup(self) -> None: ...
    async def prepare_input(self, context: RequestContext) -> dict: ...
    async def ainvoke(self, state: dict) -> dict: ...
    async def extract_output(self, final_state: dict) -> dict: ...
```

Input validation lives in prepare_input, PER PIPELINE: wrong media type ->
unsupported_media_type; empty content -> empty_input; missing file when
the pipeline requires one -> empty_input. extract_output returns the dict
that becomes the response "output" field, unmodified.

## Endpoint contract

`POST /v1/process/{pipeline}` — multipart:
- file: declared `UploadFile | None = File(None)` — OPTIONAL at the
  FastAPI layer; each pipeline enforces its own requirement in
  prepare_input. (Required File() would 422 before adapters run and
  forbid future no-file pipelines.)
- options: JSON-object string, default "{}"; unparseable or non-object ->
  invalid_options. Opaque pass-through — the server defines NO workload
  defaults.
- request_id: optional; uuid4 hex generated when absent.

Handler flow — this is the WHOLE handler, anything more is scope drift:
ready-check -> registry.get -> build RequestContext (persist upload if
present) -> prepare_input -> ainvoke -> extract_output -> canonical bytes
of the envelope -> raw Response; finally: temp-file cleanup.

Supporting endpoints:
- GET /health/live — process up.
- GET /health/ready — 503 (server_not_ready) until every loaded
  pipeline's warmup succeeded.
- GET /meta — EFFECTIVE config: pid, ready, pipelines (sorted),
  uvicorn_workers=1, executor_workers, max_inflight_requests,
  torch_threads, torch_interop_threads, applied env pins, architecture.

Response envelope — timings NEVER in the body; headers carry them
(X-Request-ID, X-Pipeline, X-Output-SHA256, Server-Timing). Raw Response
only, never FastAPI's default serializer:

```json
{"schema_version":1,"request_id":"...","pipeline":"mock-v1",
 "input":{"filename":"...","media_type":"...","size_bytes":0},
 "output":{}}
```

## Canonical encoder (service/canonical.py)

Named module constants, provisional values:
SEPARATORS=(",", ":")  ENSURE_ASCII=True  SORT_KEYS=False  ALLOW_NAN=False
Loud comment: OPEN-1 — these must byte-match the encoder that produced the
offline mt10k reference; verified in M3; flip constants to match the
reference if they differ. NaN/Inf/non-serializable -> dedicated exception
-> canonical_encoding_failed. Provide canonical_sha256(bytes).

## Error contract

`{"error":{"code","message","request_id"}}` — codes/status:
unknown_pipeline 404, server_not_ready 503, invalid_options 400,
empty_input 400, unsupported_media_type 415, payload_too_large 413,
processing_timeout 504, processing_failed 500,
canonical_encoding_failed 500. (timeout/media-type codes are declared now
even though nothing raises them until later phases — the contract is
frozen in Phase 1.)

## Upload (service/upload.py)

Stream to TEMP_DIRECTORY in 1MB chunks; enforce MAX_UPLOAD_BYTES DURING
the copy (fail at the limit, not after); empty file -> empty_input;
cleanup in finally, always. Media bytes never enter pipeline state —
state carries source_path. The temp-disk write is a deliberate production
cost; do not optimize it away.

## Settings (service/config.py)

Env-loaded, typed, validated: PIPELINES (default "document-v1" — tests
override to "mock-v1"), HOST, PORT=8100, LOG_LEVEL, BENCHMARK_MODE,
MAX_UPLOAD_BYTES, TEMP_DIRECTORY, EXECUTOR_WORKERS=4,
MAX_INFLIGHT_REQUESTS=8, REQUEST_TIMEOUT_SECONDS=300, TORCH_THREADS=1,
TORCH_INTEROP_THREADS=1. Concurrency machinery is M4 — declare and report
only. configure_runtime() sets OMP_NUM_THREADS / MKL_NUM_THREADS /
OPENBLAS_NUM_THREADS / TOKENIZERS_PARALLELISM=false via env at startup,
before any heavy import, and records what it applied (for /meta).

## Startup (FastAPI lifespan; app factory create_app(settings, builders))

load settings -> apply pins -> build selected pipelines from BUILDERS
map -> warmup every loaded pipeline -> ready=true. Startup MUST abort on:
unknown configured pipeline, duplicate registration, warmup failure.
Deployment shape:
`uvicorn service.app:app --host 0.0.0.0 --port 8100 --workers 1`.

## Mock pipeline (pipelines/mock.py)

"mock-v1", implements the full Pipeline protocol. prepare_input requires a
file. ainvoke reads source_path, returns state with result
`{"kind":"mock","input_sha256":<sha256 of file bytes>,"size_bytes":N,
"options_echo":<options>}`. Deterministic on input bytes so integration
tests assert EXACT canonical output bytes. Trivial warmup.

## Tests (exit criterion: all green)

Unit: canonical encoder (compact separators, insertion-order-independent
only if SORT_KEYS true — test current constants' actual behavior, unicode
handling per ENSURE_ASCII, NaN/Inf/non-serializable rejected, sha256);
settings (defaults, env overrides, invalid values); registry (register/
get/duplicate/unknown/sorted names); upload (chunked write, limit
enforced mid-copy, empty -> error, cleanup); error bodies and statuses.

Integration (TestClient as context manager so lifespan runs;
PIPELINES=mock-v1): /health/live; /health/ready 503 before warmup and 200
after; /meta shape and truthfulness; POST mock-v1 happy path — 200, body
equals independently computed canonical bytes, X-Output-SHA256 matches
body hash; unknown pipeline -> 404 unknown_pipeline; malformed options ->
400; non-object options -> 400; empty upload -> 400 empty_input;
oversized upload -> 413; missing file -> 400 (mock requires file); temp
directory empty after every request; a builder whose warmup raises aborts
startup.

## toil.md

Start at minute one. Log: every setup step, dependency pin, judgment call
where this spec was silent, anything that broke, time spent. Rough live
notes beat polished late ones — this file is a primary benchmark
deliverable ("total tech overhead to reach parity").

## Environment

Python venv; deps: fastapi, uvicorn, httpx, pytest, python-multipart.
Nothing else. Container is linux/amd64 under Rosetta — correctness only;
no timing from this environment is ever kept.

## Exit criteria

All tests green; server starts with PIPELINES=mock-v1; one manual curl of
any file returns canonical bytes with correct headers; /meta truthful;
toil.md has real entries. Then STOP and report back. M2 (document
pipeline) arrives as a separate handoff.
