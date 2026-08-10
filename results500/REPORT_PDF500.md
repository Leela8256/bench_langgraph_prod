# PDF-500 single-shot concurrent capacity census — RocketRide vs LangGraph

2026-08-10 · one shot per arm, arms run sequentially (RR first, recorded) ·
offered load 500 PDFs open-loop · defaults untouched (no threads=, no
max_concurrency, chunk_size unmodified) · containers 12 CPU / 10 GB each ·
git: pre-flight `3c8b9d8`, this report committed post-run.

> **Labels on every number:** emulated x86-on-arm64 — relative comparison
> only, absolute figures not portable. Latency percentiles here are
> **batch-position** (include queueing) — not comparable to closed-loop
> service latencies. Archive caveat: no LangGraph number from before Aug 8
> is comparable (the async-node image no longer exists). Single shot — no
> repeatability claims.


> **ATTRIBUTION CAVEAT (added 2026-08-10, after operator challenge):** every
> wedge observation in this project occurred under x86-on-ARM emulation.
> The stuck-jspawnhelper + livelock signature is consistent BOTH with a
> product defect AND with known emulation pathologies (fork/exec of a
> ~300-thread translated process; lock-contention degradation under
> translation). Wedge findings are therefore "reproducible in this
> environment," NOT attributed to the product, until reproduced on a native
> Linux x64 host. RocketRide ships no linux-arm64 build, so no fair native
> containerized comparison exists on Apple Silicon. The deterministic
> per-doc empty-result failures (000164/000357) are likely attribution-safe
> (clean responses, not stress behavior) but also deserve native retest.

## Headline

**Under a 500-document open-loop burst, the protocol census is 0/500
completed on BOTH arms — for opposite reasons, and the distinction is the
entire finding:**

| | RocketRide | LangGraph |
|---|---|---|
| protocol census | **0 / 500** | **0 / 500** |
| failure reason (all 500) | `wedge_affected` | client `timeout` at frozen 107.6 s |
| **server-side completions during the shot** | **0** (engine frozen) | **~61** (62 POST-200s incl. prime, steady processing) |
| what actually broke | **the engine livelocked twice** (product vs emulation attribution OPEN — see caveat) | **the protocol**: frozen timeout < queueing delay |
| census reconciliation | 500 = 500, unique ✓ | 500 = 500, unique ✓ |

### RocketRide: two wedges, zero output, full burn

Both attempts wedged at **zero completions** on a clean, warm engine
(8/8 slot warmups verified both times):

- Wedge #1: t+300 s of zero progress, 500 pending → diagnostics, backends
  reaped, pool relaunched (per preregistered protocol, one relaunch).
- Wedge #2: identical signature on attempt 2 → arm stopped, census stands.
- Shot span 625 s; send window 2.5 ms (client provably not the bottleneck —
  SDK fans out all files with no cap, verified in source).

**The wedge is a livelock, not starvation:** during 680 s of wedge the
engine burned **17.97 avg cores** (~12,000 CPU-seconds), backend RSS peaked
at **8.3 GB of the 10 GB cap** (4.7 GB at first diagnostics, 273→318
threads), and produced **zero documents**. A defunct `jspawnhelper` zombie
sat in the process tree. No error ever crossed the wire; every failure
below is the *client's* deadline, not a server signal.

Recovery did not help: the relaunch protocol (reap + fresh pool + verified
warmup) restored a working engine — which then froze again the moment the
remaining 500 were offered. Capacity under uncapped concurrent admission is
effectively **zero, reproducibly, in this emulated environment** (native
attribution open — see caveat).

### LangGraph: the server worked; the frozen protocol disqualified it

Every one of 500 records failed client-side at exactly 107.6 s (the frozen
`max(60, cal-p99×5)` timeout). Meanwhile the server:

- completed ~61 documents in the 108 s window (~0.56 docs/s under
  500-way contention, vs 1.115 docs/s at c4 calibration),
- ran the executor flat out: **17.94 avg cores**, 63 threads peak
  (executor width 22 + uvicorn machinery), RSS 1.2 → 3.8 GB peak,
- kept serving `/health/ready` throughout and returned to normal after.

This is the **formula circularity flagged in advance** (predictions P2 and
`frozen_params.json`): a per-doc timeout calibrated under c4 queueing
cannot survive 500-way batch-position queueing. Had the timeout been ≥ the
queue drain time (~15 min at observed rate), LG would plausibly have
completed most or all of 500 — but that is extrapolation, labeled as such,
not a measurement.

**Contrast that matters:** LG under overload degrades to slow-but-alive
(work completes, health endpoint responsive, memory bounded); RR under
overload degrades to burning-all-cores-forever with zero output and no
error signal.

## Resource profile (100 ms samplers, no gaps > 1 s — integrity clean)

| metric | RocketRide (during 680 s shot) | LangGraph (during 124 s shot) |
|---|---|---|
| avg cores | 17.97 | 17.94 |
| RSS start → peak → end (MB) | 137 → **8255** → 4538 | 1190 → 3849 → 3573 |
| threads peak / median | 318 / 303 | 63 / 27 |
| observed vs configured parallelism | 318 threads vs threadCount 64 | 63 threads vs executor 22 |
| successful docs / CPU-second (protocol) | 0 | 0 |
| server-side docs / CPU-second | 0 | ~0.031 (61 ÷ ~1,940 cpu-s, labeled server-side) |
| client CPU / RSS | in-container asyncio, trivial | 0.79 s / 339 MB — not saturated |

Host samplers ran throughout; no swap events observed in either window.

## Gates and validation (post-run checklist)

1. **Census reconciliation:** 500 = 500 records, unique ids, both arms ✓
2. **Drift fixture:** post-shot vectors byte-identical to pre-run values on
   both arms ✓ (embedding stack unchanged by the shots)
3. **Gate tiers:** vacuous — zero protocol completions on either arm, so
   byte/structural tiers evaluated over empty sets (0/0). Reported as such,
   not as passes.
4. **Duplicate/loss:** no duplicate results, no orphan responses ✓
5. **Sampler integrity:** zero gaps > 1 s in all four samplers ✓
6. **Predictions scored:** below.
7. **Label audit:** emulation + batch-position + archive-LG + single-shot
   labels present (top of report) ✓
8. **Git commit:** post-run commit follows this report.

## Predictions vs outcomes (`PREDICTIONS_PDF500.md`)

| prediction | outcome |
|---|---|
| P1: RR completes ≲80 then wedges out (70%) | **Directionally confirmed, optimistic.** Actual: 0 completions; wedge preceded all work, both attempts. |
| P2: LG completes all 500; amputation risk flagged | **Refuted as stated; the flagged risk is exactly what occurred.** Server processed ~61 in-window; protocol scored 0. |
| P3: 000164/000357 silent-empty *if pipe alive* | **Inconclusive** — condition never met on RR (pipe never alive under load); on LG both docs timed out with the rest. |
| P4: ≥1 wedge (95%), onset < 60 completions | **Confirmed, stronger:** 2 wedges, onset at 0 completions. |

## What this run does NOT show

It does not show LangGraph "handles 500 concurrent" (its protocol score is
also zero; only server telemetry distinguishes it). It does not measure the
working envelope (that was the cancelled stepped run; calibration data at
c4 — LG clean 50/50, RR wedged at doc ~23 — is the only envelope evidence).
It does not produce portable absolute numbers (emulation). And the RR
result binds to *this* engine build (v3.2.1) and default configuration.

## Wedge evidence trail

`runs/pdf500/rr/shot_dir/wedge{1,2}_diag.txt` (process tree at freeze),
`runs/pdf500/rr/engine_log_tail.txt`, per-doc records with wedge events
inline, samplers, and the calibration wedge (`runs/pdf500/cal-rr/`) — five
independent wedge observations across three days, two engine sessions each
wedging at different documents (nondeterministic onset, CONTEXT_SNAPSHOT
§4.1), all with the same signature: all-slot simultaneous starvation,
healthy WebSocket, zero error surface.
