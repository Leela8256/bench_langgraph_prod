# HANDOFF — Phase R: RocketRide PDF Benchmark Pipeline (Setup, Probe, Reference Capture)

Goal: finalize and verify the RocketRide side of the PDF benchmark — a
working `.pipe` for PDF -> parse -> chunk -> embed, probed with one real
PDF, with RocketRide's actual behavior captured and documented. This phase
produces NO benchmark numbers. It produces: a verified pipe file, a probe
report, captured outputs, and toil.md entries.

---

## PART 1 — How RocketRide works (read before touching anything)

### 1.1 Runtime shape

RocketRide is a declarative AI pipeline runtime: pipelines are JSON DAGs
(`.pipe` files) of typed components, executed by a C++ engine that runs
nodes cross-process. Clients never call the engine directly — they speak a
WebSocket DAP-style session protocol to the RocketRide server:

  client --(websocket: auth, use, send, events)--> server --> C++ engine
      --> per-node cross-process execution --> results/events back over
      the same websocket

The engine is booted as `./engine <abs-path>/ai/eaas.py` (NOT bare
`./engine`). The client SDK (`rocketride` Python package) handles the
session: connect -> use(pipeline) -> returns a task token -> send data
against that token -> results return as dicts.

### 1.2 Components, lanes, and pipes

- A pipeline is `{"components": [...], "project_id": "<literal uuid4>",
  "viewport": {...}, "version": 1}` in a file ending `.pipe`.
  `components` must be the FIRST field.
- Every component: `id` (pattern `<provider>_<n>`), `provider` (exact key
  from the services catalog), `config`, and `input` (array of
  `{"lane": <type>, "from": <component id>}`). Source components have no
  input.
- Data flows through TYPED LANES. Output lane of A must match input lane
  of B or the pipeline is invalid. The flows we care about:
    tags -> parse -> text
    text -> preprocessor -> documents
    documents -> embedding -> documents (with vectors)
- MIME routing: uploaded files enter on lanes by MIME type; `.pdf` maps
  to `application/pdf`. The webhook source emits `tags`; `parse` turns
  tags into `text` (it is RocketRide's native extractor for PDFs).
- `project_id` MUST be a unique literal GUID (uuid4) — never a variable,
  never reused. The engine refuses to launch the same project_id twice.
- Config profiles: most components use
  `"config": {"profile": "<name>", "<name>": {...}}`.
- Env substitution: `${ROCKETRIDE_*}` only, defined in `.env`, NOT
  allowed in project_id.

### 1.3 The authority rule (CRITICAL — this project's standing rule)

Documentation examples are NOT authoritative. The ONLY authoritative
sources for component config are, on the actual machine:
  - `.rocketride/services-catalog.json`  (all providers, lanes, invoke)
  - `.rocketride/schema/<component>.json` (Pipe.schema: profiles,
    required fields, defaults)
Any config written from docs or memory is A GUESS and must be marked
`# UNVERIFIED` until checked against those files. Phase R is largely the
act of converting guesses into verified config. If the catalog/schema
contradicts this handoff, THE CATALOG WINS — record the discrepancy in
toil.md.

### 1.4 Known engine behaviors (verified in prior benchmark work — assume
true until the probe says otherwise)

- **Splitter bug (filed):** `preprocessor_langchain` SILENTLY IGNORES
  configured `chunk_size`, `chunk_overlap`, and `length_function`. The
  engine's actual behavior is `RecursiveCharacterTextSplitter()` library
  defaults — 4000/200/len — applied to `text + "\n"`. Configure the
  preprocessor anyway (record what was configured), but expect 4000/200
  regardless. The offline reference already models this.
- **"Hi" probe:** the engine makes one unmarked probe call per pipe at
  first use. For benchmarking, the first call(s) after `use()` are
  warm-up and are classified OUT of workload metrics. For this phase it
  just means: don't be surprised by an extra call in traces.
- **Watchdog:** eaas has a ~300s idle watchdog that can reap pipes.
  Don't leave long gaps mid-probe; relaunch if state looks stale.
- **`_trace`:** pass `pipelineTraceLevel`/trace option on `use()` to get
  per-lane-write and invoke captures in the response under `_trace`.
  The probe uses this heavily.

### 1.5 SDK essentials for this phase (Python)

```python
from rocketride import RocketRideClient

async with RocketRideClient() as client:          # .env: URI + APIKEY
    result = await client.use(filepath='benchmark_pdf.pipe')
    token = result['token']
    uploads = await client.send_files(
        [('data/probe/sample.pdf', {'doc_id': 'probe-1'})], token)
    # each upload result: action/filepath/bytes_sent/file_size/
    # upload_time/result
    status = await client.get_task_status(token)   # state/progress
    await client.terminate(token)
```

- `send_files` (webhook source) is the document path. `chat()` is NOT
  used here. Validation before launch: `client.validate(pipeline=...)`
  — a pipeline with `errors` will not run.
- All SDK calls are async; never block the event loop (websocket
  keepalive dies after ~60s of blocked loop — use async I/O only).
- Exceptions: catch `RocketRideException` (subtypes: Connection/
  Authentication/Pipe/Execution/Validation). `.dap_result` has context.

---

## PART 2 — The benchmark pipeline

### 2.1 Target pipe (write as `rocketride/benchmark_pdf.pipe`)

Logical flow (mirrors the LangGraph arm's semantic stages):

  webhook -> parse -> preprocessor_langchain -> embedding_transformer
          -> response_documents

Draft — EVERY config block below is UNVERIFIED until checked against
`.rocketride/schema/*.json`:

```json
{
  "components": [
    { "id": "webhook_1", "provider": "webhook", "config": {} },
    { "id": "parse_1", "provider": "parse",
      "config": {},
      "input": [{ "lane": "tags", "from": "webhook_1" }] },
    { "id": "preprocessor_1", "provider": "preprocessor_langchain",
      "config": { "profile": "default",
        "default": { "mode": "strlen",
          "splitter": "RecursiveCharacterTextSplitter",
          "strlen": 4000 } },
      "input": [{ "lane": "text", "from": "parse_1" }] },
    { "id": "embedding_1", "provider": "embedding_transformer",
      "config": {},
      "input": [{ "lane": "documents", "from": "preprocessor_1" }] },
    { "id": "response_1", "provider": "response_documents",
      "config": { "laneName": "documents" },
      "input": [{ "lane": "documents", "from": "embedding_1" }] }
  ],
  "project_id": "<generate a fresh literal uuid4>",
  "viewport": { "x": 0, "y": 0, "zoom": 1 },
  "version": 1
}
```

Notes:
- `response_documents` is required because this is a benchmark pipeline —
  results must return to the client (no vector store; store would make it
  a terminal ingestion pipe and nothing would come back).
- No LLM, no vector DB, no control connections needed.

### 2.2 Schema verification checklist (do BEFORE first launch)

For each of `webhook`, `parse`, `preprocessor_langchain`,
`embedding_transformer`, `response_documents` read
`.rocketride/schema/<name>.json` and record in the probe report:
1. Exact provider key exists in services-catalog.json (spelling!).
2. Supported input/output lanes match the pipe above.
3. Required config fields and available profiles.
4. **parse**: does it expose profiles/options selecting the PDF parser
   or extraction behavior? This is the highest-value question in the
   whole phase — if a configurable extraction mode exists, list every
   option; the choice affects cross-framework comparability.
5. **embedding_transformer**: can the model be pinned, and can it be set
   to `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` (CPU, 384-dim,
   normalized)? Record the exact profile/field names, the DEFAULT model
   if none is set, and whether normalization is configurable. If the
   MiniLM model cannot be pinned, STOP and surface it — embedding parity
   with the LangGraph arm depends on this.
6. Fix the pipe file to verified reality; mark every field VERIFIED with
   the schema file it came from.

Then `client.validate()` the pipe and clear all errors before `use()`.

### 2.3 Probe stage (one PDF, full capture)

Use one known text-rich PDF (`data/probe/sample.pdf` — pick any clean
born-digital PDF; record its SHA-256).

1. Launch with trace enabled. Capture the FIRST call after use()
   separately (expected: engine warm-up behavior).
2. `send_files` the probe PDF. Capture the complete response and
   `_trace`.
3. Record, exactly:
   - Extracted text as parse produced it (recoverable from trace/lane
     writes or from chunk contents): save to
     `probe/rr_extracted_text.txt` with SHA-256.
   - Chunk count, each chunk's text (save all, hashed), observed
     chunk-size behavior — CONFIRM or REFUTE the 4000/200-on-text+'\n'
     expectation on PDF-extracted text. Test: configure strlen=4000 as
     above AND run once with strlen=512 configured; if chunk boundaries
     are identical both times, the ignore-bug is confirmed live.
   - Vector dimensionality, first 8 values of first vector, whether
     vectors appear L2-normalized (compute the norm).
   - Timing fields the response/trace exposes (for later decomposition
     mapping).
4. Run the SAME PDF a second time (fresh pipe copy, fresh uuid4).
   Assert byte-identical extracted text and chunks across the two runs —
   RocketRide-side determinism is a gate for everything later.
5. **Embedding-parity fixture:** send a small PLAIN TEXT file (`.txt`,
   fixed known content, record it) through the same pipe (text MIME
   skips PDF parsing). Save the returned vectors. These get compared
   `allclose` against the LangGraph arm's vectors for the same text —
   proving same-model-same-vectors INDEPENDENT of extractor differences.

### 2.4 Why extraction parity is NOT expected (context, not a task)

RocketRide's `parse` and the LangGraph arm's pypdf are different
extractors; their text output will differ. That is accepted methodology:
each framework runs its native production extractor; gates are
within-framework determinism + completion; cross-framework equivalence is
REPORTED (char counts, chunk counts, deltas), not gated; embedding parity
is proven separately via 2.3.5. Do not try to force the outputs to match
and do not treat the mismatch as a bug. DO document exactly what parse
produced so the comparison table can be built later.

---

## PART 3 — Deliverables and exit criteria

Deliverables (all under `rocketride/` in the working tree):
1. `benchmark_pdf.pipe` — verified, validate()-clean, launches, fresh
   literal uuid4.
2. `probe/PROBE_REPORT.md` — the schema-verification checklist results
   (esp. parse options and embedding model pinning), full probe
   captures, determinism assertion result, the strlen=512 vs 4000 chunk
   comparison, warm-up call observations, timing fields available.
3. `probe/` artifacts: rr_extracted_text.txt (+sha256), chunks dump
   (+hashes), vectors sample, embedding-parity fixture text + vectors.
4. `run_probe.py` — the script that did all of the above, rerunnable.
5. toil.md entries — every schema surprise, every doc-vs-reality
   mismatch, every judgment call, timestamped. This is a primary
   deliverable ("total tech overhead"), not bookkeeping.

Exit criteria: pipe launches and processes the probe PDF end to end;
determinism assertion passed; embedding model question ANSWERED (pinned,
or surfaced as blocker); probe report complete. Then STOP and report.
Open questions that materially change the benchmark design (parse
options, model pinning failure) are surfaced, never silently resolved.

## Out of scope for Phase R

gd100 corpus fetch (Phase C), offline pypdf reference (Phase F), the
LangGraph PDF pipeline (Phase L), any measured benchmarking (Phase B),
any change to the LangGraph FastAPI server.
