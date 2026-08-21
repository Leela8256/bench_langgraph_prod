# LangGraph Video Arm — Architecture

The detailed design of `arms/langgraph/`: how the service is layered,
what each graph node does, how concurrency actually works (and why this
arm reached ~19 cores where the engine holds ~6), and every deliberate
design decision with its reason.

## 1. The three-layer design

```
┌───────────────────────────────────────────────────────────────────┐
│ service.py — FastAPI transport shell                              │
│   POST /process · GET /health/ready · GET /meta                   │
│   (maps to RocketRide's webhook + response_documents — transport  │
│    is the measurement boundary, identical on both arms)           │
├───────────────────────────────────────────────────────────────────┤
│ graph.py — LangGraph StateGraph (the orchestration under test)    │
│   START → frames → detect → chunk → embed → assemble → END        │
├───────────────────────────────────────────────────────────────────┤
│ workload/ — pure computation, no fastapi/langgraph imports        │
│   frames.py · detect.py · chunk.py · embed.py                     │
└───────────────────────────────────────────────────────────────────┘
```

Same layering as the PDF arm (`aws_bench/arms/langgraph`), for the same
reason: the *framework* (LangGraph) only orchestrates; the computation
lives in plain modules that could run without it. That keeps the
comparison honest — we benchmark LangGraph's orchestration of the same
work, not a different implementation of the work.

Node names mirror the RocketRide pipe one-to-one:

| graph node | RR component | function |
|---|---|---|
| `frames` | frame_grabber_1 | video → ~121 PIL images |
| `detect` | detect_1 | images → JSON detection lines |
| `chunk` | preprocessor_1 | lines → ≤4000-char chunks |
| `embed` | embedding_1 | chunks → 384-dim vectors |
| `assemble` | response_1 (assembly half) | documents payload |

RocketRide's transport components (webhook, MIME routing) are
deliberately NOT graph nodes — they map to the FastAPI layer. Matched
measurement boundaries, not matched internal topology.

## 2. The graph in detail

```python
class VideoState(TypedDict, total=False):
    video_path: str          # input (temp file path)
    frames: list             # PIL images — DROPPED after detect (see below)
    det_lines: list[str]     # one JSON line per frame
    chunks: list[str]
    embeddings: list[list[float]]
    documents: list[dict]    # final payload
    timings: dict            # per-node seconds, accumulated
```

- **Linear graph, no checkpointer.** This is a stateless
  request/response pipeline; persistence machinery would be dead weight
  and an unfair overhead vs the engine.
- **Each node returns a partial state update** (LangGraph merges it) and
  appends its wall time to `timings` — that is where the per-node
  decomposition in the reports comes from (decode ~55%, detect ~40%,
  embed ~5% on the box), something the closed engine cannot provide.
- **`detect` returns `frames: []`** — a ~121-image list of 352×288 RGB
  frames is ~30 MB of state; dropping it immediately after use keeps a
  concurrent-request memory footprint flat.
- Graph is **compiled once at service startup** and reused by every
  request (compilation is not free; per-request compilation would be a
  self-inflicted framework tax).

## 3. Each node's implementation

**frames — `workload/frames.py`**
- `ffmpeg -nostdin -loglevel error -i <video> -vf fps=1/15 -f image2 f_%06d.png`
  into a `TemporaryDirectory`, then each PNG → PIL RGB.
- **Why the `fps` filter:** it is what the engine's own reader uses
  (interval 15 s → fps=1/15 internally), and it produced *identical
  frame counts* to the engine on every compared video (102=102 on
  ES2016d; 28/28 frame-parity in the head-to-head).
- **Why PNG, not JPEG:** lossless. JPEG would perturb the pixels the
  detector sees and shift borderline detection scores.
- ffmpeg binary: system `ffmpeg` if present (apt in the Docker image,
  brew on the Mac), else the pip `imageio-ffmpeg` bundled binary — the
  same fallback trick the engine itself uses.

**detect — `workload/detect.py`**
- `rfdetr` package, `RFDETRBase` (the same backend the engine's
  detection module prefers), threshold 0.3.
- **Lazy singleton behind a `threading.Lock`** — the lock guards model
  *loading* only (one ~130 MB checkpoint load, once); inference itself
  is not serialized (see §5).
- COCO class names from `rfdetr.assets.coco_classes` (≥1.9 layout, dict
  keyed by class id) with a fallback import for older releases.
- Output per frame: one JSON line, engine format exactly —
  `[{"label", "score", "box":{x1,y1,x2,y2}, "centroid":{x,y}}, …]`,
  serialized with `json.dumps` defaults, which is byte-what the engine
  emits (verified against a captured engine response).

**chunk — `workload/chunk.py`**
- `RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=0)`
  over `"\n".join(det_lines)`.
- Parameters were **recovered empirically**: 4000/0 reproduces the
  engine's chunks byte-exactly on the reference capture; 4096 and 3600
  do not. In detection-dense rooms a single frame line exceeds 4000
  chars and the splitter cuts mid-line — chunks are text windows, not
  guaranteed-valid JSON, on BOTH arms (faithful replication includes
  the quirk).

**embed — `workload/embed.py`**
- `SentenceTransformer("sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
  device="cpu")`, `normalize_embeddings=True`, lazy singleton.
- **This exact model, not all-MiniLM-L6-v2** — it is what the engine's
  `miniLM` profile resolves to (its `services.json`; independently
  confirmed by reproducing engine vectors to 1.06e-07). The wrong MiniLM
  passes every dimension/norm check and is silently incomparable.

**assemble** — builds
`[{"page_content", "embedding", "metadata": {"chunkId": i}}, …]`.

## 4. The service shell (`service.py`)

- **Lifespan**: builds the compiled graph, then `_warmup()` — a
  synthetic 352×288 frame pushed through detect → chunk → embed. Only
  after that does `/health/ready` return 200. Reason: model loading
  (~seconds) must never be paid inside a measured request; the drivers
  gate on readiness before sending anything.
- **`POST /process`**: streams the multipart upload to a
  `NamedTemporaryFile` in 4 MB chunks (no whole-file buffering), then
  runs the graph via `anyio.to_thread.run_sync` — the CPU-bound work
  happens in a worker thread so the event loop keeps answering health
  checks. Response: documents + `n_frames`, `n_chunks`, `total_chars`,
  `output_sha256` (hash of all chunk text, used by the smoke's
  determinism checks), and the per-node `timings`. Temp file deleted in
  a `finally` — this arm accumulates **no scratch** (contrast: the
  engine retains every upload until container removal).
- **`GET /meta`**: the arm's identity for provenance — frame interval,
  detector + threshold, splitter params, embedding model + dim.

## 5. Concurrency model — why c6 reached ~19 cores

- **One uvicorn process**; each in-flight request occupies one worker
  thread running one graph invocation. Six concurrent requests = six
  concurrent graph invocations.
- The model singletons are **shared, not duplicated**: one RF-DETR and
  one MiniLM in memory regardless of concurrency (measured anon RSS
  ~2.8 GB under c6 — flat).
- Parallelism comes from two places: **torch releases the GIL during
  inference**, so six threads genuinely run six inferences across
  cores, with torch's intra-op threading spreading each one further
  (unpinned in the smoke); and **ffmpeg decodes are separate
  subprocesses**, fully parallel.
- Net measured effect (head-to-head, identical videos and concurrency):
  **19.5 effective cores vs the engine's 5.45**, with near-identical
  per-unit CPU cost — i.e., the same work, executed with ~3.6× more
  parallelism. 225 OS threads spawned vs the engine's ~1,000 — fewer
  threads, more work done.
- In the matched benchmark the envelope pins `OMP_NUM_THREADS=1` on
  both arms, which removes torch's intra-op spread; document-level
  concurrency (the c\<N\> window) remains the variable under test.

## 6. Determinism properties (measured)

- Same video, same host → **byte-identical output** (rep pairs produced
  identical `output_sha256`; embedding digests identical).
- Across hosts (Mac vs box): outputs differ ~±1% (different
  ffmpeg builds and torch numerics flip borderline detections) — which
  is why all gates compare within-platform and all arm-vs-arm
  comparisons run box-vs-box.

## 7. Packaging & operations

- `Dockerfile`: `python:3.12-slim-bookworm` + apt `ffmpeg` + pip
  requirements (langgraph, langchain-text-splitters, fastapi, uvicorn,
  rfdetr, sentence-transformers, pillow, imageio-ffmpeg). Port 8200.
  `HF_HOME`/`TORCH_HOME` point into `/root/.cache`, which compose mounts
  from the shared `rr-model-cache` volume — model weights download once
  per box and are shared with the RocketRide container.
- Versions float within majors for smokes; **pin with `pip freeze`
  before measured benchmark runs** (the rfdetr 1.9 `assets` import move
  is exactly the kind of drift pinning prevents).
- Local dev: `arms/langgraph/.venv` (python3.12), `smoke_lg.py` drives a
  running service with structural checks + an informal comparison
  against the RocketRide reference capture.
- Driven in benchmarks by `bench/lg_driver.py` — same `per_doc.jsonl`
  record schema as the RocketRide driver, modes seq / c\<N\> (its native
  ingestion is per-request HTTP; there is no batch API, and that
  asymmetry is documented rather than papered over).
