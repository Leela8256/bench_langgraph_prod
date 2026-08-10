# PDF-500 pre-registered predictions

Committed 2026-08-10 ~12:00 PDT — after pre-flight probe + calibration,
BEFORE the shot. Frozen params: LG timeout 107.6 s; RR timeout 1500 s (see
circularity note in `runs/pdf500/frozen_params.json`). Emulated environment;
relative claims only.

Calibration evidence in hand: LG 50/50 @c4 (p50 1.57 s, p99 21.5 s,
1.115 docs/s). RR wedged DURING calibration @c4 on a clean engine: 22 fast
completions (p50 2.1 s) then 28 consecutive timeouts across all 8 slots.

## P1 — RR admission behavior at 500 offered (inherited P2-a/P2-b)

The calibration already tilts this: the wedge starved all 8 connections
simultaneously → **server-side pipeline limit, not per-connection
multiplexing (P2-b-flavored). Predict: the shot completes 15–40 docs, then
wedges; after the one permitted relaunch, completes a similar small batch,
then wedges again → RR census ends with ≲80 completed of 500 (confidence
70%).** The ~4-slot admission question is subsumed: admission never gets a
chance to matter because the wedge arrives first.

## P2 — LG post-sync-fix cliff at 500

Calibration at c4 shows the executor dispatching cleanly. 500 concurrent
HTTP requests is above the old image's collapse point but the sync-node fix
moves compute off the event loop. **Predict: LG completes all 500** —
no cliff at 500 — with heavy queueing: batch span ~450–700 s
(≈1500 s of work at ~2–3 effective cores... revised: span 400–800 s),
batch-position p99 near the span, TTFR < 5 s, zero failures (confidence
65%; failure mode if wrong: client timeouts at 107.6 s amputating the queue
tail — which the frozen formula makes possible since queueing at 500 ≫
queueing at c4-calibration; flagged in advance as the known formula risk).

## P3 — Deterministic silent-empty docs

`000164.pdf` and `000357.pdf` return success-shaped empty results
("no documents") on RR **if the pipe is alive when they are attempted**;
if the wedge precedes them they will be wedge_affected instead. LG
processes both normally (they extract fine under pypdf). Confidence 85%
conditional on a live pipe.

## P4 — RR wedge probability and position

**Predict: ≥1 wedge with probability ~95%; first onset within the first
60 completions (calibration onset was at ~23).** Signature: completions
stop across ALL slots simultaneously; WS stays connected; no error frames.
Recovery via relaunch restores throughput briefly; second wedge within a
similar doc count → arm stopped per protocol. Both wedge events land in the
census as `wedge_affected`, distinguished from ordinary timeouts.

## Resource predictions (per handoff metric list)

- RR: effective cores near-zero after wedge onset (idle-starved), peak
  early; backend RSS 0.7–1.5 GB, possible runaway growth on the wedge doc
  (2.6 GB precedent). Threads ~200 steady (64-thread instance + runtime).
- LG: effective cores 2.5–5 during the shot (executor 22 wide but GIL +
  torch-internal serialization caps it), peak RSS 3–5 GB under 500 queued
  multiparts, threads ~30–40 peak.
- Client saturation: not expected on either arm (LG client CPU was 0.3 s
  per 100 docs in the 1K run; RR client is in-container asyncio).
