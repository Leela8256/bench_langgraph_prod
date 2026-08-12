# toil.md — LangGraph arm, build log

---

## Session 4 — 2026-08-07 — Containerization (≈35 min)

M6 work ("Docker: pinned image, one worker, fixed resources") landing early by
direct request, before its handoff exists. Built to the deployment shape the
existing handoffs already pin. Details and the RocketRide side in
`../DOCKER.md`; RocketRide-specific toil in `../rocketride/toil.md`.

- **Image `prodbench-langgraph:latest`, 3.06 GB**, `linux/amd64`. amd64 is not
  a preference: the RocketRide engine ships linux-x64 only, so matching
  architecture is a precondition for comparability.
- **The embedding model is baked in at build time**, then `HF_HUB_OFFLINE=1`
  and `TRANSFORMERS_OFFLINE=1` at runtime. Without this the first container to
  start would download weights over the network mid-benchmark, and a hub
  outage or a re-uploaded model would silently change vectors. `/meta` now
  reports `hf_hub_offline: "1"` so the guarantee is observable, not assumed.
- **Thread pins are `ENV` in the image**, not entrypoint flags — they must be
  set before torch initializes. `configure_runtime()` re-applies them and
  `/meta` confirms all four.
- Verified in-container: `architecture: x86_64`, `pypdf 6.15.0`, split config
  `4000/200/len`, and a real PDF POST returning 200 with per-stage
  `Server-Timing`.

### Judgment calls

- **`.dockerignore` excludes `tests/`**, so the test fixtures are not in the
  image — but `pipelines/document_pdf/fixtures/warmup.pdf` IS, because
  `warmup()` runs it through the full graph at startup and readiness depends
  on it. Startup would fail if that fixture were ignored.
- **Resource limits live in compose, not the Dockerfile**, as a matched pair
  with the RocketRide arm (2.0 CPU / 4 GB each). Verified applied via
  `docker inspect`. Changing one arm alone invalidates the comparison.
- **Healthcheck hits `/health/ready`, not `/health/live`** — readiness is
  warm-up gated, so the container reports healthy only after the graph has
  actually run once. `start_period: 60s` covers model load under emulation.

### ⚠ Finding: the embedder truncates at 512 tokens, so ~20% of every chunk
### is never embedded (affects BOTH arms identically)

Found by sending the SAME PDF through both containers. RocketRide produced 5
chunks (3966 chars each), this arm produced 6 (3127 chars each) — different
text, different lengths, only a **77-char common prefix** — yet:

```
cosine similarity(RR chunk0 vector, LG chunk0 vector) = 1.0000000154
max abs diff = 1.49e-08
```

Identical vectors from different text is only possible if the model never saw
the difference. Confirmed: `multi-qa-MiniLM-L6-cos-v1` has
**`max_seq_length = 512` tokens**. Sweeping prefix lengths, the vector stops
changing entirely past ~3000–3500 chars for this text:

```
chars 3000 -> cos vs previous = 0.98234701   (still changing)
chars 3500 -> cos vs previous = 1.00000000   (saturated)
chars 4000 -> cos vs previous = 1.00000000   (saturated)
```

Consequences, in order of importance:

1. **The tail of every 4000-char chunk is unsearchable.** For a real RAG
   system this is a correctness bug, not a tuning detail: chunk_size is
   ~4000 chars but the embedder only ever sees the first 512 tokens.
2. **It does NOT bias the comparison** — same model, same limit, both arms.
   Parity is preserved.
3. **But it makes chunk-boundary parity partly trivial.** Two arms that chunk
   differently can still produce identical vectors, so "vectors match" is
   weaker evidence of same-work than it appears. Chunk text/offset hashes,
   not vectors, are the real chunking gate.

Not fixed here: chunk_size is a PINNED equality mirroring RocketRide's
ignored-config behavior, so changing it would break the replication rule.
Surfaced for the benchmark design — it belongs in the methodology notes, and
Phase B should not claim "embedding parity" as strong evidence without it.

### Open

- Emulated on this arm64 host: correctness only, no reportable timing.
  Embedding dominated a containerized request (~1.4–1.6 s of ~1.65 s) versus
  ~36 ms native — that gap is emulation overhead, and it is exactly why
  nothing here is quotable.

---

## Session 3 — 2026-08-07 — Phase L: document-pdf-v1 (≈60 min)

Built the real compiled LangGraph pipeline replicating the RocketRide
benchmark pipe's work. M2's workload functions did not exist, so they were
built here to the M1–M3 spec — one implementation, shared by the text and PDF
pipelines.

### LangGraph is back (deliberately)

Phase 1 banned `langgraph` imports outright; Phase L requires them. The ban
was Phase-1-scoped, so this is a lifting, not a conflict. `langgraph 1.2.10`
is installed and `pipelines/document_pdf/graph.py` compiles a real
`StateGraph` with four nodes, no checkpointer, compiled once at construction.
The Phase-1 claim that "a compiled graph satisfies `ainvoke` natively" is now
actually proven: the adapter just forwards to `graph.ainvoke`.

### Two real bugs found, both in things Phase 1 shipped

1. **Startup was not repeatable.** Running lifespan twice against the same app
   died with `RegistryError: pipeline 'document-pdf-v1' already registered`,
   because the registry was built in `create_app` and never reset. Uvicorn
   hides it (lifespan runs once), and M1's tests missed it because every test
   built a fresh app. Fixed properly — `PipelineRegistry.clear()` and a
   `registry.clear()` at the top of lifespan — rather than papering over it
   with a function-scoped test fixture. Regression test added.
2. **My own overlap assertion was wrong.** I asserted every consecutive chunk
   pair overlaps. It doesn't: `RecursiveCharacterTextSplitter` only carries
   overlap where it must break *within* a separator group. The first
   `text_page.pdf` had pages under 4000 chars, so each page became one chunk
   and overlap never occurred at all. Fixed the fixture (oversized pages force
   intra-page splitting) AND the assertion (at least one overlapping pair,
   every overlap ≤ 200). The failing test was right and my expectation was
   wrong — worth recording, because a laxer assertion would have shipped a
   fixture that never exercised overlap.

### Pinned equalities — one now empirically proven cross-arm

- **Chunking**: `RecursiveCharacterTextSplitter()` constructed with NO
  arguments; `effective_split_config()` asserts 4000/200/len in a test and
  `/meta` reports it. Applied to `text + "\n"`.
- **Embedding**: `sentence-transformers/multi-qa-MiniLM-L6-cos-v1`, CPU,
  384-dim, `normalize_embeddings=True`, numpy float32 → Python `float`.
  **Cross-arm parity measured**: this arm's vector for the Phase R plain-text
  fixture vs RocketRide's, max abs diff **1.34e-07**, cosine similarity
  **1.0000000784**, allclose at atol=1e-6/rtol=1e-5. Same model, same vectors,
  independent of extractor differences — exactly what the fixture was for.

### Judgment calls where the handoff was silent

- **`/meta` gained `workload_versions`** carrying pypdf, the embedding model
  info, and the effective split config. The handoff only required the pypdf
  version; reporting the split config and model id there makes the pinned
  equalities externally auditable at runtime instead of trusted.
- **`_workload_versions()` degrades gracefully.** If workload deps are absent
  (a mock-only deploy), `/meta` reports `"unavailable: ImportError"` rather
  than 500-ing. `/meta` must never be the thing that breaks.
- **pypdf is pinned `==6.15.0`**, not `>=`. It defines the offline reference;
  a float would silently invalidate parity artifacts.
- **Slow tests are marked `slow`** (model load / full graph) but still run by
  default — marking without deselecting keeps the suite honest while allowing
  `-m "not slow"` for a fast loop.
- **Fixture hashes recorded in `tests/fixtures/FIXTURES.md`** with an explicit
  warning that changing them invalidates Phase F artifacts.
- **`corrupt.pdf` is a truncation**, not an encrypted PDF. Encryption needs a
  real crypto writer; truncation exercises the same path (pypdf raises → node
  propagates → server maps to `processing_failed`), verified live at 500.

### Census observations (not measurements)

- The empty-extraction path is real and clean: `no_text.pdf` → `""` → zero
  chunks → zero vectors → canonical body `{"chunks":[],"vectors":[]}` at HTTP
  200. Not an error, as specced; the census will need to distinguish it from a
  failure.
- Per-stage timings land in `Server-Timing` as
  `extract / chunk / embed / assemble / total`, so extraction stays separable
  in analysis — the accepted extractor inequality does not contaminate the
  other stages.
- Shape only, never reportable: on one live request `embed` dominated
  (~36 ms of ~41 ms total) with `extract` ~2.6 ms. Emulation-free here (native
  arm64) but single-sample and unpinned — no conclusions.

### Open items

- **gd100 parity is NOT verified.** `GD100_REFERENCE_DIR` is unset, so two
  parity tests skip with a loud reason stating explicitly that a green suite
  does not imply parity. Needs Phase F.
- `HF_HUB_OFFLINE` is respected but unset here, so the first model load
  reached the network. For reproducible runs it should be set with a
  pre-warmed cache.
- RocketRide's `parse` duplicates ~3.3% of lines (Phase R finding). Extraction
  is an accepted inequality, but a *defect* on one side is not the same as a
  methodology difference — flagging that the comparison table should note it.

### Time

≈60 min: ~15 deps + model prefetch (torch/sentence-transformers is the long
pole), ~15 workload + graph + adapter, ~20 tests and the two bugs above, ~10
fixtures, docs, live verification.

---

Running log of setup steps, dependency pins, judgment calls, breakages, and
time spent. A primary benchmark deliverable: this file is the record of
"total tech overhead to reach parity", so rough live notes beat polished
late ones. Times are approximate wall-clock, not instrumented.

---

## Session 2 — 2026-08-06 — rework onto HANDOFF-phase1-generic-server.md (≈25 min)

A second Phase 1 handoff replaced the first. Compared old vs new before
touching code; the HTTP contract was unchanged but the internals differed.
What changed, and why:

### Removed LangGraph entirely (the big one)

The new handoff's first hard prohibition is "NO langgraph import anywhere in
Phase 1"; permitted deps are exactly fastapi, uvicorn, httpx, pytest,
python-multipart. Session 1 had deliberately built the mock as a compiled
`StateGraph` (verbal instruction at the time was "we need langgraph as
server reachable via fastapi"). Flagged the contradiction to Leela rather
than resolving it silently; instruction was to implement the new handoff, so
LangGraph came out.

- `pipelines/mock.py` is now a plain class implementing the Pipeline
  protocol. Real compiled graph arrives in M2 and replaces only this class.
- Rebuilt the venv from scratch rather than `pip uninstall langgraph` — an
  uninstall leaves transitive deps (langchain-core, orjson, jsonpatch…)
  behind, and "nothing else" should be verifiable with `pip freeze`.
- COST: the session-1 claim "exercises the compiled-graph-satisfies-ainvoke
  claim from day one" is now unproven until M2. Accepted — the protocol's
  `ainvoke` signature is the same shape a compiled graph exposes.

### Added the generic pipeline contract (`service/pipeline.py`)

New module: `RequestContext` dataclass (HTTP-side facts, NOT graph state)
and a four-method `Pipeline` protocol — `warmup`, `prepare_input`,
`ainvoke`, `extract_output`. Session 1 had a duck-typed registry needing
only `ainvoke`, with the handler building a fixed `MediaState` TypedDict.

- DELETED `MediaState` from `schemas.py`. A fixed state TypedDict in
  `service/` contradicts the principle that adapters build graph-specific
  state; `schemas.py` now holds only the envelope. Side benefit: drops the
  `typing_extensions` import.
- Input validation moved out of the server into `prepare_input`, per
  pipeline. The server no longer decides what a pipeline accepts.

### `file` is now optional at the FastAPI layer

Changed to `UploadFile | None = File(None)`. Session 1 used a required
`File(...)`, which returned FastAPI's raw 422 (`{"detail":[...]}`) for a
missing file — outside our error contract. Session 1 logged this as an open
judgment call; the new handoff resolves it explicitly: a required `File()`
would 422 before adapters run and would forbid future no-file pipelines.
Now missing file -> mock's `prepare_input` -> 400 `empty_input`. Verified.

### Smaller conformance fixes

- `sha256_hex` -> `canonical_sha256`.
- `pipelines/shared/` -> `pipelines/agent/` (new layout names `agent`).
- Added `README.md` (required by the new layout, absent before).
- Added `tests/unit/test_upload.py` — the new handoff requires UNIT coverage
  of upload (chunked write, limit enforced mid-copy, empty, cleanup);
  session 1 covered upload only through integration tests.
- OPEN-1 comment updated to name the **mt10k** reference and M3 as the
  verification point, with the explicit rule: if the reference differs, flip
  our constants — do not adapt the reference.

### Judgment calls where the new spec was silent

- **Registry validates the full protocol at registration**, not just
  `ainvoke`. The spec only lists register/get/duplicate/unknown/sorted for
  the registry, but "startup MUST abort on duplicate registration" implies
  fail-at-startup over fail-on-first-request. A half-implemented adapter now
  dies in lifespan with a message naming the missing methods.
- **`warmup` is mandatory**, not optional-if-present as in session 1 — the
  protocol declares it, so the registry calls it unconditionally.
- **Options are parsed before the upload is persisted.** The spec's handler
  flow doesn't place it, but `RequestContext.options` must exist before the
  context is built, and it means malformed options fail without touching
  disk.
- **`media_type` is `file.content_type` with no fallback** (session 1
  defaulted to `application/octet-stream`). The new `RequestContext` types
  it `str | None`, so synthesizing a value would be a lie in the envelope.
- **Timings still read from `final_state["timings_ns"]`**, merged with a
  handler-measured `total`. `extract_output` returns only the output dict,
  so timings need a separate channel; reading a well-known key off final
  state keeps the server modality-agnostic.
- **`unsupported_media_type` (415) and `processing_timeout` (504) are
  declared but unraised** — per the handoff, the contract is frozen in
  Phase 1 even though nothing triggers them until later phases.

### Breakages / friction

- First run of the new upload unit tests needed an `anyio_backend` fixture
  (`tests/conftest.py`) to pin async tests to asyncio; without it anyio's
  plugin parametrizes over trio too, which isn't installed.
- The initial smoke script compared two request hashes without pinning
  `request_id` — they differed because the generated uuid4 is part of the
  body. Not a service bug; the script now pins the id.
- VS Code reports "Package fastapi is not installed" against `pyproject.toml`
  because the IDE interpreter isn't `.venv`. Cosmetic; tests and server run.

### Dependency pins (post-rebuild, `pip freeze`)

Direct: fastapi 0.141.1, uvicorn 0.52.1, python-multipart 0.0.32,
httpx 0.28.1, pytest 9.1.1.
Transitive: starlette 1.4.1, pydantic 2.13.4, pydantic_core 2.46.4,
anyio 4.14.2, h11 0.16.0, httpcore 1.0.9, certifi 2026.7.22, click 8.4.2,
idna 3.18, annotated-types 0.8.0, annotated-doc 0.0.5, typing_extensions
4.16.0, typing-inspection 0.4.2, iniconfig 2.3.0, packaging 26.3,
pluggy 1.6.0, Pygments 2.20.0. No langgraph — verified with `pip show`.

---

## Session 1 — 2026-08-05 — initial M1 build (≈30 min)

Built against the first handoff (`HANDOFF-phase1.md`). Most of it survived
the rework: `canonical.py`, `errors.py`, `config.py`, `upload.py` and the
whole HTTP surface were unchanged by the new spec.

### Setup

- Dev machine system Python is 3.9.6; `uv` not present. Used Homebrew
  `python3.12` for the venv. The M6 container should pin its own Python and
  not inherit this choice.
- Repo scaffolded to the handoff layout in one pass.

### Decisions that still stand

- **Multipart/form-data request schema** (`file` + `options` JSON string +
  `request_id`) rather than JSON-with-base64 or a raw body. Spooled
  multipart uploads generalize to video-sized inputs without holding bytes
  in memory, and the server pays the same temp-disk cost RocketRide pays —
  which keeps the comparison honest.
- **Error responses go through the canonical encoder too**, so there is
  exactly one serializer in the codebase.
- `MAX_UPLOAD_BYTES` default of 104857600 (100 MiB) — the spec declares the
  setting but no default. Revisit when video lands.
- `TEMP_DIRECTORY` defaults to `tempfile.gettempdir()`.
- `/meta` reports `request_timeout_seconds`, `max_upload_bytes`,
  `temp_directory` and `benchmark_mode` beyond the fields the spec lists —
  "effective config" read broadly.
- `Server-Timing` format: `<name>;dur=<ms>` entries, comma-joined, sorted by
  name; `graph` from the pipeline plus handler-measured `total`.
- `uvicorn_workers` is a fixed constant `1` in Settings (deployment shape),
  not env-configurable.

### Superseded by session 2

- Mock as a compiled LangGraph `StateGraph` — removed, see above.
- `MediaState` TypedDict — deleted.
- Required `file` / 422 on missing file — now optional / 400.

---

## Open items

- **OPEN-1** (live): canonical encoder flags are PROVISIONAL and must
  byte-match the encoder that produced the offline mt10k reference.
  Resolved in M3. No parity number is trustworthy until then.
- **Environment** (live): container is linux/amd64 under Rosetta on an M5
  Pro. Correctness only — no timing from this environment is ever reported.
- **OPEN-2 from session 1 — likely dead**: the first handoff required
  ratifying the multipart shape with Shashi (RocketRide) and Ansh
  (LlamaIndex) as a shared cross-framework schema. The new handoff frames
  the benchmark as LangGraph vs RocketRide only and never mentions
  ratification. Left here rather than deleted: if a LlamaIndex arm still
  exists, the request/response shape is still unratified.
