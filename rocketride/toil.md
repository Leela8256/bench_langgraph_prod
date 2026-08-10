# toil.md — RocketRide arm

---

## 2026-08-07 — Containerization (≈35 min, both arms)

Not a handoff phase — a direct request. For the LangGraph side this is M6
work ("Docker: pinned image, one worker, fixed resources") landing before its
handoff exists; built to the deployment shape the existing handoffs already
pin. See `../DOCKER.md` and `../docker-compose.yml`.

### The engine checksum: my bug, not a supply-chain problem

First build failed on `sha256sum -c` for the downloaded tarball. Initial read
was "upstream replaced the pinned asset" — **wrong, and I said so before
checking.** The download was intact (227,226,866 bytes, valid gzip, full
content-length). The truth: RR_BENCH's `EXPECTED_SHA` is the hash of the
**extracted `engine` binary**, not of the tarball. Confirmed by hashing the
local binary that Phase R actually ran against:

```
sha256(/bench/rr-lg-v4/engine/engine) = cf1fbf9ce72d15fef0257daaf0bfef130a263535be08c7160a0e0dada0371316
                       EXPECTED_SHA  = cf1fbf9ce72d15fef0257daaf0bfef130a263535be08c7160a0e0dada0371316
```

Second bug in the same instruction: `--strip-components=1`. The archive is
FLAT (`ai/`, `engine`, `include/`, `java/`, `lib/`, `nodes/`, `pip/`,
`rocketride/`, `static/`), so stripping a component would have destroyed the
layout. Both fixed; the build now extracts first and verifies the binary.

Lesson worth keeping: a checksum mismatch has at least three causes
(corrupt download, changed artifact, wrong thing hashed) and I jumped to the
scariest one. Verify which artifact the hash refers to before alleging
tampering.

### Containerized engine reproduces Phase R exactly

The important gate, since the container downloads its own engine rather than
using the volume Phase R ran against:

```
Phase R (volume engine)   extracted sha = f6b70e3d67648678e44fe9f12775ca20a73754021c5e821059150a264d9d6b8c
containerized engine      extracted sha = f6b70e3d67648678e44fe9f12775ca20a73754021c5e821059150a264d9d6b8c
```

Same 5 chunks, same lengths, same 384-dim normalized vectors, splitter-ignore
still confirmed. The image is a faithful replacement for the ad-hoc volume
setup, so `prodbench-rr` is no longer load-bearing.

### Judgment calls

- **Engine downloaded at build time, not copied from the volume.** The volume
  copy is machine-local and its `engine/` had grown to 7.8 GB with run caches;
  a pinned URL + verified binary hash is reproducible anywhere. Image is
  280 MB.
- **`ROCKETRIDE_APIKEY=local-dev` is an ENV.** Docker warns about secrets in
  ENV; this is the local engine's literal placeholder, not a credential. Left
  as-is deliberately — it documents that no real key is involved.
- **Default CMD is `serve`** (engine as a long-lived service, the shape a
  benchmark driver needs); `probe` runs the Phase R capture instead.
- Artifacts need a mount: `docker compose run --rm -v "$PWD/rocketride:/work"
  rocketride probe`, otherwise probe output dies with the container.

### Open

- Both containers run **emulated** on this arm64 host. Correctness only.
  A native x86-64 host is still owed before any timing is quotable.
- Resource limits (2 CPU / 4 GB each) are a matched pair, verified applied via
  `docker inspect` (`NanoCpus=2000000000`, `Memory=4294967296`). Changing one
  arm without the other silently destroys comparability.

---

# Phase R

Running log: setup steps, judgment calls, schema surprises, doc-vs-reality
mismatches, everything that broke. A primary deliverable ("total tech
overhead"), not bookkeeping. Times are approximate wall-clock.

The LangGraph arm keeps its own log at `../langgraph-fastapi/toil.md`.

---

## 2026-08-07 — Phase R: setup, probe, reference capture (≈70 min)

### 1. Setup — no install needed, but nothing documented either

The `.rocketride/docs/` set is entirely client-side: it tells you to
`pip install rocketride` and point `ROCKETRIDE_URI` at
`https://api.rocketride.ai`. **There is no documented way to run the engine
locally.** Phase R needs local, so the boot recipe had to be recovered from
prior benchmark work rather than from docs.

Found it in `~/Desktop/RR_BENCH` (an existing RocketRide-vs-LangGraph harness
that already has published results): the engine is a pinned, sha256-verified
GitHub release binary (`rocketride-server` v3.2.1, linux-x64), booted as
`./engine <ENGINE_DIR>/ai/eaas.py` with `ROCKETRIDE_URI=ws://127.0.0.1:5565/task/service`
and `ROCKETRIDE_APIKEY=local-dev`. Requires `libc++1`/`libc++abi1`.

- Engine + `venv_rr` (SDK 1.3.0) already existed in the `rrbench-work` docker
  volume, so no download was needed. Boot took **2.0 s** warm.
- **Judgment call: copied engine + venv_rr into a NEW volume `prodbench-rr`**
  rather than booting against `rrbench-work`. Booting writes to the engine
  cache, and contaminating RR_BENCH's evidence volume to save a 1 GB copy is a
  bad trade. Copied to the identical path `/bench/rr-lg-v4/...` so the venv's
  shebangs and absolute paths still resolve.
- Host is macOS arm64; the engine is linux-x64 and RR_BENCH hard-asserts
  Linux x86_64. Everything runs in a `linux/amd64` container under emulation.
  Fine here — **Phase R produces no numbers by design**.
- The host's running `rr-mysql` container is unrelated to the engine; nothing
  in the harness references MySQL.

### 2. The authority rule earned its keep

**Four of the five config blocks in the handoff draft were wrong** against
`.rocketride/schema/*.json`. Per §1.3 the catalog wins; recorded here:

| component | handoff draft | schema reality |
|---|---|---|
| `webhook` | `config: {}` | requires `hideForm`, `type`, `mode` |
| `preprocessor_langchain` | `strlen: 4000` | **no such field in any of 8 profiles** |
| `embedding_transformer` | `config: {}` | `profile` is required |
| `response_documents` | `laneName: documents` | ✅ correct |
| `parse` | `config: {}` | ✅ correct (schema has no properties at all) |

Biggest surprise: **`preprocessor_langchain` exposes no chunk-size, overlap, or
length-function field anywhere.** `strlen` appears 48 times in the schema but
only as an enum value of `mode` (a length *unit*: chars vs tokens). So the
filed "silently ignores chunk_size" bug understates it — those fields were
never configurable in the first place.

### 3. What broke

- **`async with RocketRideClient()`** (the docs' pattern) raised
  `TypeError: does not support the asynchronous context manager protocol`.
  Root cause was mine: `sys.path.insert(0, ENGINE_DIR)` shadowed the SDK's
  `rocketride` package with the engine's internal one. Removed the path insert
  and switched to explicit `connect()`/`disconnect()`.
- **`validate(pipeline=<dict>)` is unreliable.** In a fresh session it returns
  the normalized pipeline (clean). Inside the probe run it returns
  `[{"ccode": 40, "message": "'pipeline' is missing or invalid"}]` for the
  *same dict that the engine then launches and runs successfully*. Not
  root-caused; it correlates with calling `validate()` after a prior `use()` in
  the same session. **Judgment call: validate() is advisory in `run_probe.py`,
  logged but never fatal** — a picky validator must not mask a pipeline the
  engine actually runs. Left as an open item; passing a JSON *string* instead
  of a dict fails differently (`'str' object has no attribute 'get'`), so the
  dict form is correct.
- **My own harness bug, caught before it wrote a false result:** the first
  probe run returned zero documents for every run, and the determinism gate
  reported `true` — because two empty lists compare equal. A gate that passes
  on no output is worse than no gate. Now every gate requires non-empty output
  first. The underlying cause was `extract_documents()` not understanding the
  real `send_files` shape (a list of upload results each wrapping
  `result.documents`).
- Switched launching from `use(pipeline=<dict>)` to `use(filepath=<stamped
  copy>)` — the path the SDK docs and the prior benchmark driver both use.
  Fresh `uuid4` is stamped into each copy before launch.

### 4. Judgment calls where the handoff was silent

- **Deliverables live at `prod bench/rocketride/`, not inside
  `langgraph-fastapi/`.** The handoff file was dropped into the LangGraph repo
  but says its own out-of-scope is "any change to the LangGraph FastAPI
  server", so the RocketRide arm gets a sibling directory. Flagging in case the
  intended location was different.
- **`preprocessor_1` takes `text` from BOTH `parse_1` and `webhook_1`.** The
  handoff wants the embedding-parity `.txt` fixture through "the same pipe",
  but a `.txt` enters webhook on the `text` lane and never reaches `parse`. The
  extra input edge is what makes one pipe serve both paths. Verified working:
  the PDF path and the `.txt` path both produce documents.
- **The probe PDF is generated, not sourced.** `make_sample_pdf.py` writes a
  deterministic born-digital 6-page PDF with stdlib only. Using a real PDF off
  the machine would have meant committing someone's document and a fixture
  nobody else can reproduce. Regeneration is byte-identical; xref offsets are
  validated.
- **The `strlen=512` test was adapted, not skipped.** With no size field to
  vary, it was run as "inject the undeclared field the handoff drafted and see
  what the engine does" — which still answers the underlying question. Output
  was byte-identical, confirming the ignore behavior live.
- Trace level passed as `str(TRACE_SUCCESS)` i.e. `"1"`; `pipelineTraceLevel`
  is typed `str` in the SDK but the constants are ints. No `_trace` came back
  either way — open.

### 5. Findings worth escalating

- **`parse` duplicates content deterministically** — 8 of 248 output lines are
  duplicated at a regular per-page stride, verified against a fixture where
  each line appears exactly once. ~3.3% inflation of any downstream corpus.
  This is a correctness defect, not an extractor-choice difference.
- **`datalab_parse`**, which `parse`'s own description recommends for
  provenance-bearing extraction, **does not exist** in this install.
- **`custom` embedding profile caps `model` at 32 chars.** The required
  `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` is 47. It only worked
  because the built-in `miniLM` profile already resolves to exactly that model
  (engine confirms per-document via `embedding_model`). Any future embedder
  with a long HF name is unpinnable through `custom`.

### 6. Good news

Chunking matched expectation exactly (4000 size, ~150–166 char real overlap =
library defaults snapping to separators), determinism passed on the first
honest run, vectors are 384-dim and L2-normalized, and the engine self-reports
the embedding model so parity is verifiable rather than assumed.

### 7. Time

≈70 min total: ~15 schema verification, ~10 locating the local-boot recipe,
~10 fixture generation, ~20 harness debugging (the three breakages above),
~15 report and log.
