# PDF-200 pre-registered predictions

Committed 2026-08-08 16:20 PDT — after Stage 0 and the RR ground-truth
capture began, BEFORE probe, calibration, and all measured reps. Emulated
environment; all quantities relative.

Known going in: Stage 0 found a SHARED pipeline instance (same token ×3
clients, one backend worker, ~202 observed threads ≈ default threadCount 64)
→ pool=8. LG executor width 22 (cpu_count 18 + 4). LG nodes are now sync
(executor-dispatched) — the 1K run's event-loop collapse should not recur.

## P1 — Throughput by level (docs/s, emulated, ratio is the claim)

| level | LangGraph | RocketRide | ratio LG:RR |
|---|---|---|---|
| 1 | ~0.4–0.6 | ~0.35–0.5 | ≈1 (parity; both serialize) |
| 4 | ~1.2–1.8 | ~1.0–1.6 | ≈1–1.3 |
| 16 | ~2.5–4 | ~1.5–3 | LG ahead ~1.3–2× |
| 64 | ~2.5–4 (plateau ≈ level 16) | see P2 | LG ahead unless P2-b |

LG plateaus between 16 and 64: executor width 22 and 12 CPUs bound it;
GIL + torch internals will keep effective cores well under 12.

## P2 — The RocketRide-64 question (offered load = threadCount)

The 1K burst's ~4-slot ceiling was observed with ALL traffic on ONE
connection. Stage 0 shows the engine advertises threadCount 64 on a shared
instance. Two hypotheses this run disambiguates:

- **P2-a (per-connection multiplexing limit):** with 8 connections × 8
  in-flight each at level 64, effective concurrency ≈ min(64, 8×~4) ≈ 32 —
  RR at L64 clearly beats RR at L16 and completes all docs.
- **P2-b (per-pipeline admission limit):** effective concurrency stays ~4
  regardless of connections — L16 and L64 throughput ≈ L4, and excess
  in-flight requests risk the stall/lost behavior again (recorded failures,
  not hangs, thanks to completion-proof timeouts).

**Prediction: P2-a (60% confidence).** The probe's 4 completions matched
default `use()` threads on a single session; 8 separate sessions should
multiply the admitted work. If P2-b holds instead, that is the product
finding of the run.

## P3 — TTFR per level

RR TTFR < LG TTFR at every level (RR first result ~2–3 s; LG first doc
~1–3 s but behind model-warm HTTP path; expect near-tie at L1, RR ahead at
higher levels since its first slots start immediately).

## P4 — CPU / RSS per doc

- LG: ~1 core-equivalent per in-flight doc up to executor saturation;
  container RSS growing to ~2.5–3.5 GB at L64 (temp files + threads).
- RR: backend worker RSS ~0.7–1.2 GB steady; CPU scaling with admitted
  concurrency only (flat beyond its ceiling per P2 outcome).

## P5 — Correctness

Both arms byte-identical to their own ground truth at every level
(concurrency must not change chunking). Any level where hashes differ by
level is a major finding (nondeterminism under load).

## P6 — Failure behavior

LG: zero failures expected at all levels post-fix. RR: zero at L1/L4;
at L16/L64 failures only if P2-b, and then as recorded
completion_proof_missing timeouts, never silent hangs (harness guarantees
the recording; the product's own surface for them is still silence).
