# Generic LangGraph Benchmark Server

Production-style FastAPI service that will host OSS LangGraph pipelines, built
as the LangGraph arm of a benchmark against RocketRide.

**Phase 1 (M1) — contract & scaffolding. This is what exists today.**
Transport, contracts, registry, and the pipeline abstraction, proven against a
mock pipeline. There is no LangGraph, no model, and no real processing yet —
by design. The point of this phase is that every later pipeline (document,
PDF, image, video, agent graphs) drops in WITHOUT changing the server, and
every test written now stays valid verbatim.

## Architecture principle

FastAPI owns transport. `PipelineRegistry` owns discovery. Pipeline adapters
translate requests into graph-specific state. OSS LangGraph (later) owns
orchestration. `workload/` (later) owns computation.

FastAPI must remain unaware of graph topology, so ANY compiled LangGraph —
sequential, branching, fan-out, loops, agent — can be added later without
redesigning the server. There is no modality-specific logic anywhere in
`service/`, and only `service/canonical.py` may serialize canonical response
data.

## Layout

```
service/     app.py config.py registry.py pipeline.py schemas.py
             canonical.py errors.py upload.py
pipelines/   mock.py          (document/ image/ video/ agent/: empty, later phases)
workload/    empty — M2
client/      empty — M5
tests/       unit/ integration/
toil.md      running build log — a primary benchmark deliverable
```

## Run

```bash
python3.12 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx pytest python-multipart

PIPELINES=mock-v1 .venv/bin/uvicorn service.app:app \
  --host 0.0.0.0 --port 8100 --workers 1
```

One uvicorn worker is the deployment shape, not a default.

```bash
.venv/bin/python -m pytest -q     # exit criterion: all green
```

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /v1/process/{pipeline}` | multipart: `file` (optional at this layer; each pipeline enforces its own requirement), `options` (JSON-object string, default `"{}"`), `request_id` (optional; uuid4 hex generated when absent) |
| `GET /health/live` | process up |
| `GET /health/ready` | 503 `server_not_ready` until every loaded pipeline's warmup succeeded |
| `GET /meta` | effective config: pid, ready, pipelines, worker/thread counts, applied env pins, architecture |

`options` is opaque pass-through — the server defines NO workload defaults.

Responses are canonical bytes via a raw `Response`, never FastAPI's default
serializer. **Timings never appear in the body**; headers carry them:
`X-Request-ID`, `X-Pipeline`, `X-Output-SHA256`, `Server-Timing`.

```json
{"schema_version":1,"request_id":"...","pipeline":"mock-v1",
 "input":{"filename":"...","media_type":"...","size_bytes":0},
 "output":{}}
```

Errors are stable: `{"error":{"code","message","request_id"}}` with
`unknown_pipeline` 404, `server_not_ready` 503, `invalid_options` 400,
`empty_input` 400, `unsupported_media_type` 415, `payload_too_large` 413,
`processing_timeout` 504, `processing_failed` 500,
`canonical_encoding_failed` 500. The contract is frozen in Phase 1, so the
timeout and media-type codes are declared before anything raises them.

## Adding a pipeline

Implement the `Pipeline` protocol in [service/pipeline.py](service/pipeline.py)
— `warmup`, `prepare_input`, `ainvoke`, `extract_output` — and add a builder to
the `BUILDERS` map. A compiled LangGraph graph satisfies `ainvoke` natively.
Input validation belongs in `prepare_input`, per pipeline; the server does not
decide what a pipeline accepts.

## Open items

- **OPEN-1**: the canonical encoder flags in `service/canonical.py` are
  PROVISIONAL. They must byte-match the encoder that produced the offline
  mt10k reference; that is verified in M3. No parity number is trustworthy
  until then.
- Timings from the dev environment are never kept — the container is
  linux/amd64 under Rosetta. Correctness only.

## Roadmap

M2 document pipeline (real compiled LangGraph) · M3 parity · M4 runtime
controls · M5 benchmark driver · M6 Docker · M7 RocketRide comparison ·
M8 image/video/agent pipelines.
