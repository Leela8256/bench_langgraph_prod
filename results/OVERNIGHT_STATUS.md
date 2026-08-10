# PDF-1K overnight run — status log

Timestamps are local (PDT). Append-only.

- **02:28** Run begins. Deadline 07:00 local → 4.5 h budget. Host recorded:
  Apple M5 Pro, 18 cores / 24 GB RAM; Docker VM 18 vCPU / 21.2 GB.
  **Both images are linux/amd64 under emulation (arm64 host)** — every
  timing in the report carries the emulated label; relative comparison only.
- **02:29** Prior sequential/bulk parity-run data archived to
  `runs/prior-parity-run/` (superseded by this handoff, kept per
  never-delete rule). Its state: LG 200/200 ok; RR 32 ok + wedge findings
  (000163.pdf backend blowup to 2.6 GB RSS; terminate() does not reap).
- **02:30** Corpus extension started: threads 002–006 downloading in
  parallel (need ~800 more valid PDFs on top of the 200).
- **02:30** Compose updated to the preregistered 12 CPU / 12 GB per arm.
  12+12 vs 18-core host is on the record; arms run one at a time.
- **02:30** Interpretation decisions (no one to ask, logged here):
  1. **RR pipeline includes `parse`** (webhook→parse→preprocessor→
     embedding→response_documents) — the handoff's component list omits
     parse but its workload line says "PDF → parse → split"; PDFs cannot
     reach the preprocessor's text lane without parse. Using the
     Phase-R-verified pipe unchanged.
  2. **LG submission = 1000 concurrent HTTP POSTs** to the existing
     `document-pdf-v1` endpoint. The handoff mentions "one invocation
     fanning out via Send", but the submission-model section mandates an
     open-loop burst of 1000 sends, and building a new Send-fanout
     pipeline would violate "never modify app code" against the arm we
     have. Dispatch mechanism recorded as provenance, not claims.
  3. **RR correctness gate** cannot be byte-exact vs a pypdf ground truth
     (different extractor by accepted methodology). Gates adapted per arm:
     LG = byte-exact vs offline pypdf+splitter reference; RR = rep-over-rep
     byte-consistency + all-docs-returned + vector sanity + sampled
     allclose of RR vectors vs offline model applied to RR's own chunk
     texts. Logged as methodology adaptation.
- **02:50** Ground truth done: 999/1000 extractable (1 excluded from byte
  gates). Stage 0 pass: identical model id + dim both arms; drift fixture
  (pre) captured.
- **03:00** PROBE FINDING (gate did its job): RR completed only 4/10 under
  10 concurrent sends; the other 6 NEVER completed (300 s timeout) while
  survivors took ~5 s. Verified against SDK source: send_files(list)
  internally gathers per-file coroutines with NO client-side cap ("let
  server handle queuing") — mechanically identical to our driver, so the
  stall is server-side admission behavior, not client artifact. Archived
  parity-run data corroborates: bulk send_files(10) calls also timed out;
  per-doc sequential fallback succeeded 8/10.
  DECISION: keep the frozen open-loop submission; RR mass timeouts under
  burst ARE the default-behavior result. LG probe: 10/10 ok, byte-exact
  vs ground truth.
- **03:05** Calibration begins (100-doc burst per arm, same clean-restart +
  prime procedure as reps; provisional 900 s timeout for calibration only —
  frozen per-arm timeouts derive from its p99).
- **03:07** PREDICTIONS_PDF1K.md committed (pre-calibration — stricter than
  required). cal-lg result: **VALID — 100/100 ok, byte-exact vs ground
  truth, span 61 s, 1.64 docs/s, TTFR 27.3 s** (emulated). Much faster than
  the parity-run extrapolation (that average was skewed by heavyweight
  docs; open-loop lets the server interleave I/O with compute).
- **03:08** Autonomous chain launched (detached): cal-rr → freeze →
  rep1-lg (full) → rep1-rr (until 06:40 cutoff) → post drift fixture.
  ORDER DEVIATION from strict RR→LG alternation, reason logged in the
  chain script: LG rep1 is the night's only potentially-valid rep; RR
  mass-stalls under burst (probe, SDK-source-verified), so securing the
  one valid rep takes priority under the deadline. RR rep1 runs second.
- **03:08** PREDICTED (not yet observed): frozen LG timeout will be
  ~max(60, 61×5)=305 s; in a 1000-doc open-loop burst, every doc still
  queued at t=305 s will fail by client timeout — the frozen per-doc
  formula interacts destructively with batch-position queueing on a
  serializing server. Formula stays frozen per handoff; the interaction
  is a methodology finding for the report.
- **05:40** cal-rr: INVALID, **0 records in 2.5 h**. Prime doc succeeded
  (1.5 s, engine healthy), then 100 concurrent send_files produced NOTHING —
  not even client-timeout records, though each task carried a 900 s
  asyncio.wait_for. At 10 concurrent (probe) timeouts still fired; at 100
  the SDK client's event loop wedges outright. Escalation of the admission
  stall: somewhere in 10→100 concurrency the client itself stops making
  progress. Engine-side: unknown; will capture container logs after the
  measured reps finish (not touching containers mid-measurement).
  HARNESS ANOMALY (logged, not fixed mid-run): the 10-min watchdog took
  2.5 h to fire — its docker-exec polls crawled against the wedged
  container (~450 s per 30 s-nominal iteration). The 06:40 hard cutoff
  bounds worst case regardless.
- **05:40** Timeouts frozen per formula: lg=304.8 s (61 s cal p99 × 5);
  rr=60 s (formula floor — no calibration data existed). rep1-lg running.
