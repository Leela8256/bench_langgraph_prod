# Phase R — RocketRide PDF Pipeline Probe Report

Date: 2026-08-07 · Engine: local `./engine ai/eaas.py` on `ws://127.0.0.1:5565/task/service`
SDK: `rocketride` 1.3.0 · Host: linux/amd64 container (Rosetta on macOS arm64)

**Status: pipeline verified, launched, and processed the probe PDF end to end.
All gates passed. The embedding-model question is ANSWERED — not a blocker.**

Reproduce with:

```bash
docker run --rm --platform linux/amd64 \
  -v prodbench-rr:/bench -v "$PWD:/work" -w /work \
  -e ROCKETRIDE_APIKEY=local-dev \
  rrbench-dev:latest /bench/rr-lg-v4/venv_rr/bin/python run_probe.py
```

---

## 1. Schema verification checklist (§2.2)

Authority: `.rocketride/services-catalog.json` (143 providers) and
`.rocketride/schema/<component>.json`. All five provider keys exist with the
exact spelling used in the pipe. Lane chain verified end to end:

```
webhook(_source→tags) → parse(tags→text) → preprocessor_langchain(text→documents)
   → embedding_transformer(documents→documents) → response_documents(documents→∅)
```

| Component | Verified against | Required config | Handoff draft was |
|---|---|---|---|
| `webhook` | `schema/webhook.json` | **`hideForm`, `type`, `mode`** | ❌ `{}` — would omit 3 required fields |
| `parse` | `schema/parse.json` | none — schema has **no properties at all** | ✅ `{}` correct |
| `preprocessor_langchain` | `schema/preprocessor_langchain.json` | `profile`; `default` needs **`splitter`, `mode`** | ❌ included `strlen: 4000`, **not a schema field** |
| `embedding_transformer` | `schema/embedding_transformer.json` | `profile` (default `miniLM`) | ❌ `{}` — `profile` is required |
| `response_documents` | `schema/response_documents.json` | `laneName` (default `documents`, maxLen 32) | ✅ correct |

Corrected, verified pipe: [`../benchmark_pdf.pipe`](../benchmark_pdf.pipe).

### 1.1 `parse` options — the highest-value question (§2.2.4)

**Answer: `parse` exposes ZERO configuration.** Its entire Pipe schema is:

```json
"Pipe": { "schema": { "title": "Parse", "type": "object" }, "ui": {} }
```

No properties, no profiles, no PDF-parser selector, no extraction-mode switch.
There is nothing to tune and nothing to pin — extraction behavior is whatever
the engine build does.

Two consequences for cross-framework comparability, both from the component's
own description:

- Output is **flattened to Markdown** and **does not preserve cell-level
  provenance** (no page boundaries, no bbox coordinates, no table-HTML grid).
  It is explicitly documented as *"not suitable for audit-grade or
  provenance-bearing extraction."*
- It names `datalab_parse` as the structure-preserving alternative — but
  **`datalab_parse` does not exist** in this install: absent from
  `services-catalog.json` and there is no `schema/datalab_parse.json`.
  Documented-but-missing; recorded as a discrepancy.

Alternative parsers that DO exist on the `tags → text` lane, if extraction
choice ever needs to change: `llamaparse`, `landing_ai_parse`, `reducto`
(all third-party/keyed), plus `ocr` on a different lane shape.

### 1.2 `embedding_transformer` model pinning (§2.2.5) — RESOLVED

**The required model is available and IS the default.** Profiles are
`custom | miniAll | miniLM | mpnet`, with `miniLM` the schema default. The
engine reports the resolved model back on every returned document:

```
embedding_model: "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
```

That is exactly the model Phase R requires — CPU, 384-dim, and observed
L2-normalized. So `"config": {"profile": "miniLM"}` pins it, and the identity
is verifiable per-document at runtime rather than assumed.

**Latent constraint worth recording:** the `custom` profile *cannot* express
this model name. Its `model` field carries `"maxLength": 32`, and
`sentence-transformers/multi-qa-MiniLM-L6-cos-v1` is **47 characters** — 15
over. `custom` also requires all four of `model`, `truncate_dim`,
`document_prefix`, `query_prefix`. This does not block Phase R (the named
profile covers it) but it does mean **any HF model with a name longer than 32
chars is unpinnable through `custom`** — a real limit if the benchmark ever
needs a different embedder. Not surfaced as a blocker; surfaced as a ceiling.

### 1.3 `preprocessor_langchain` — no chunk-size knob exists

Across **all 8 profiles** (`default, recursive, character, markdown, latex,
nltk, spacy, custom`) the only fields are `mode` (`strlen|tokens`), `splitter`,
and per-profile extras (`separators`, `separator`, `model`). Searching the raw
schema:

```
chunk_size: 0 occurrences   chunk_overlap: 0   length_function: 0   overlap: 0
```

`strlen` appears 48 times but **only as an enum value of `mode`** (a length
*unit* selector: characters vs tokens), never as a field. So the handoff's
`"strlen": 4000` is not a schema field, and there is no size to configure.

---

## 2. Probe captures (§2.3)

Fixture: [`../data/probe/sample.pdf`](../data/probe/sample.pdf) — a deterministic,
born-digital, 6-page/240-line PDF generated by
[`make_sample_pdf.py`](../data/probe/make_sample_pdf.py) (stdlib only, no personal
content, byte-identical on regeneration).

- PDF sha256 `c00543f59e914f79bb65b2bc3b966904df4a7ea48324e398f5435787c24e0410`, 22 565 bytes
- Source text embedded in it: 18 776 chars over 240 unique lines

### 2.1 What `parse` produced

| | value |
|---|---|
| extracted chars | **19 667** |
| sha256 | `f6b70e3d67648678…` (full in `probe_capture.json`) |
| non-empty lines | **248** (source has 240) |
| separators | `\n\n` ×238, `\n\n\n\n` ×5 (page gaps), `\n` ×4 |
| non-ASCII | none |
| page/bbox markers | none (consistent with the flatten-to-Markdown note) |

Saved verbatim to [`rr_extracted_text.txt`](rr_extracted_text.txt).

### 2.2 ⚠ `parse` duplicates content — reproducible extraction defect

The 8 extra lines are **duplications**, not formatting. Four two-line blocks are
emitted twice, at a regular stride (p02l09-10, p03l17-18, p04l25-26, p05l33-34 —
+8 lines per page):

```
p02l09 occurrences in the PDF bytes: 1   → in parse output: 2
p03l17 occurrences in the PDF bytes: 1   → in parse output: 2
p04l25 occurrences in the PDF bytes: 1   → in parse output: 2
p05l33 occurrences in the PDF bytes: 1   → in parse output: 2
```

Verified against the source: each string appears exactly once in the PDF (240
`Tj` operators for 240 unique lines), so this is not a fixture artifact. It is
**deterministic** — both runs produced identical chunk hashes, so the
duplication reproduces exactly.

Impact: ~3.3% of extracted lines are duplicated, inflating char counts, chunk
counts and any downstream retrieval corpus. This is a correctness issue in
RocketRide's extractor, distinct from the accepted "different extractors give
different text" premise in §2.4 of the handoff. **Surfaced, not worked around.**

### 2.3 Chunking — 4000/200 CONFIRMED

| | |
|---|---|
| chunks | **5** |
| lengths | 3966, 3967, 3969, 3955, 3806 |
| measured overlaps | 166, 161, 159, 152 chars |

Max chunk 3969 ≤ 4000, and every boundary carries real overlap just under 200 —
exactly `RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)`
backing off to a separator boundary. **The 4000/200-library-defaults expectation
is CONFIRMED on PDF-extracted text.** Chunks + hashes in
[`rr_chunks.json`](rr_chunks.json).

### 2.4 Splitter-ignore test — adapted, and confirmed

The handoff's "run once with `strlen=512`" is not literally runnable: there is
no size field to set (§1.3). The test was run in the form that preserves its
intent — inject the undeclared `strlen: 512` exactly as the handoff drafted it,
and observe whether the engine rejects or silently ignores it:

| run | config | chunk hashes |
|---|---|---|
| `pdf_run_a` | schema-clean `default` profile | 5 chunks |
| `pdf_run_c_strlen512` | + `"strlen": 512` injected | **byte-identical to run_a** |

The engine accepted the pipe and produced identical output. **The
silently-ignores behavior is confirmed live** — and it is stronger than filed:
the field is not merely ignored, it is not part of the schema at all.

### 2.5 Determinism gate — PASSED

Two runs, same PDF bytes, **fresh uuid4 `project_id` each**:

```
run_a: 5 chunks  f1f01e54…, 6dab33a9…, 10b4f675…, 0bc3c5f4…, 329481ad…
run_b: 5 chunks  f1f01e54…, 6dab33a9…, 10b4f675…, 0bc3c5f4…, 329481ad…
identical: true
```

Note the gate requires non-empty output before it can pass — an earlier harness
revision reported "determinism: true" when *both* runs returned zero documents.
Fixed; see toil.

### 2.6 Vectors

| | |
|---|---|
| dimensionality | **384** |
| model (engine-reported) | `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` |
| L2 norm (PDF chunk 0) | 0.99999996 → **normalized** |
| L2 norm (parity fixture) | 1.00000004 → **normalized** |
| first 8 (parity fixture) | 0.003873, 0.028066, 0.067240, 0.068548, 0.021021, 0.000376, 0.044998, −0.018209 |

Normalization is **not configurable** — there is no normalize field in the
schema; it is simply what the model/engine emits.

### 2.7 Embedding-parity fixture (§2.3.5)

`parity_fixture.txt` (122 bytes, sha256 `e851210aa2fda06a…`) sent through the
same pipe. A `.txt` enters webhook on the `text` lane and bypasses `parse`
entirely — this required wiring `preprocessor_1` to accept `text` from **both**
`parse_1` and `webhook_1` (recorded as a judgment call in toil).

Result: 1 document, `page_content` byte-identical to the input, 384-dim
normalized vector. Saved to [`parity_vectors.json`](parity_vectors.json) for
`allclose` comparison against the LangGraph arm's vector for the same string.

### 2.8 Returned document shape

```
keys: embedding, embedding_model, metadata, page_content, type
metadata: chunkId, isDeleted, isTable, nodeId, objectId, parent,
          permissionId, signature (128 hex chars, all zeros here), tableId
type: "Document"
```

`chunkId` gives chunk ordering; `objectId`/`parent` give document lineage.

### 2.9 Warm-up and timing fields

- **No unmarked "Hi" probe call was observable** in this pipeline. The handoff
  expects one per pipe at first use; this pipe has no LLM component, which is
  the likely reason. `get_task_status()` right after `use()` returns
  `state: 3, completed: false, "Webhook ready - system is ready to accept
  requests"` — the pipe idles until data arrives.
- **Timing fields available:** `send_files` returns per-file `upload_time`
  (1.66 s first call, 0.17–1.0 s after); task status carries `startTime` /
  `endTime` epoch floats. No per-node timing was exposed.
- **`_trace` was NOT returned** despite passing
  `pipelineTraceLevel=TRACE_SUCCESS` ("1") on `use()`. No `_trace` key appears
  anywhere in the response. Either the level constant needs a different form or
  trace surfaces over the event channel rather than the result. **Open — needed
  before per-stage decomposition in a later phase.**
- Engine boot: **2.0 s** (warm cache). Wall times recorded here are shape-only
  and must never be reported — emulated arm64→amd64.

---

## 3. Exit criteria

| criterion | status |
|---|---|
| Pipe launches and processes the probe PDF end to end | ✅ |
| Determinism assertion passed | ✅ (5/5 chunk hashes identical) |
| Embedding model question answered | ✅ pinned via `miniLM` = the required model |
| Probe report complete | ✅ this file |
| No benchmark numbers produced | ✅ |

## 4. Items surfaced, not silently resolved

1. **`parse` duplicates ~3.3% of extracted lines**, deterministically (§2.2).
2. **`parse` has zero configuration** and flattens to Markdown without page or
   bbox provenance (§1.1).
3. **`datalab_parse` is documented but absent** from this install (§1.1).
4. **`custom` embedding profile caps model names at 32 chars**, so most HF
   names are unpinnable there; only the built-in profiles reach long names (§1.2).
5. **No chunk-size/overlap field exists** in any preprocessor profile (§1.3).
6. **`_trace` did not materialize** with `pipelineTraceLevel` (§2.9).
7. **`validate()` returns `ccode 40 "'pipeline' is missing or invalid"`** for
   the same dict that validates cleanly in a fresh session — see toil §3.
8. **Four of five handoff draft config blocks were wrong** against the schemas
   (§1) — the authority rule earned its place.
