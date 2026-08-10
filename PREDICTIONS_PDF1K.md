# PDF-1K pre-registered predictions

Committed 2026-08-08 03:08 PDT — after the 10-doc probe, BEFORE calibration
results and before any measured rep. Informed by: probe (10 docs), the prior
200-doc sequential parity run, and SDK source inspection. Environment is
emulated (linux/amd64 on arm64) — all quantities relative, not portable.

## P1 — Batch completion

- **LangGraph** will complete all (or nearly all) 1000 docs *if* the frozen
  per-doc timeout exceeds the full batch span; otherwise the tail beyond the
  timeout will fail with client timeouts. Predicted batch span: **2.5–4 h**
  (extrapolating the parity run's ~12 s/doc emulated average; requests
  serialize on the single event loop).
- **RocketRide** will complete only a small fraction under open-loop burst:
  roughly **the first ~4 concurrent slots' worth of work processed at a few
  seconds per doc**, with the overwhelming majority of the 1000 stalling at
  pipe-open and dying at the frozen timeout. Predicted completion:
  **<10% of docs**; predicted span ≈ the frozen timeout itself.

## P2 — Throughput ratio (batch throughput, fixed definition)

Meaningless to compare directly if RR mass-fails; predicted:
- LG: ~0.07–0.11 docs/s (emulated).
- RR: dominated by timeout failures; successful-doc throughput over the span
  ≈ 0.01–0.05 docs/s. **LG "wins" by default completing, not by speed** —
  the interesting RR number is the ~4-slot concurrency ceiling itself.

## P3 — Time-to-first-result

RR first results in **~5 s** (first 4 slots complete quickly — engine
parallelism beats the serialized LG loop for the head of the batch).
LG first result in **~10–60 s** (first doc must wait for its turn through
upload multiplexing but embeds are serialized; small first docs).
**RR wins TTFR decisively; loses everything after the first ~4 docs.**

## P4 — CPU / memory

- LG: CPU pinned near 1 core-equivalent (serialized compute, OMP=1);
  RSS roughly flat ~1.5–3 GB (model + request buffers for 1000 queued
  uploads; possible growth from 1000 spooled temp files).
- RR: burst of engine CPU early (4 workers), then idle-with-stalled-pipes;
  backend RSS ~0.6–1.5 GB; risk of the known runaway-RSS behavior if any
  poison doc lands in the first slots.

## P5 — Failure behavior

- LG: failures, if any, are clean per-doc HTTP errors (4xx/5xx or client
  timeout); no collateral damage between docs; server stays healthy.
- RR: stalled docs fail ONLY by client timeout — the server surfaces no
  error at all for them (silent admission stall). If a poison doc processes,
  expect the Phase-R wedge signature (pipe dead, WS healthy). Error surface
  will show LG structured `{"error":{...}}` vs RR nothing/timeouts.

## P6 — Correctness of what does complete

Both arms byte-deterministic on completed docs: LG exactly matching offline
ground truth (probe: 10/10); RR self-consistent rep-over-rep, model id
reported per doc, norms ≈ 1, dim 384.
