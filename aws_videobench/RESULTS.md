# Video Benchmark — Results to date (2026-08-21)

The two cross-arm runs completed so far, reported under the final V0–V5
metric suite (METRICS.md / METRICS_EXPLAINED.md). Both runs are
**unpinned, single-rep sizing evidence** — the matched enveloped 3-rep
campaign is still ahead; do not quote these as final benchmark results.

Raw records (every number re-derivable forever):
- Run A: `s3://rocketride-benchmark-data/leela/videobench/h2h-20260821T195300Z/`
- Run B: `s3://rocketride-benchmark-data/leela/videobench/native60-20260821T210828Z/`

## Run configurations

| | Run A — "c6 head-to-head" | Run B — "native saturation" |
|---|---|---|
| corpus | ami30test: 28 measured + 2 warm (14.51 h) | ami30h: 60 measured + 2 warm (31.43 h) |
| RocketRide mode | c6 (6 in flight, per-video calls) | **blast** (whole backlog, one send_files — its native path) |
| LangGraph mode | c6 (6 in flight, HTTP) | **c60** (whole backlog at t=0 — its native path) |
| envelope | none — 32 cores unpinned, threads unpinned | same |
| reps | 1 | 1 |
| pipe | benchmark_video_detect.pipe (frames 15 s → rfdetr 0.3 → split 4000/0 → multi-qa-MiniLM 384) | same |

## V1 — Throughput

| metric | A: RR (c6) | A: LG (c6) | B: RR (blast) | B: LG (c60) |
|---|---|---|---|---|
| **x_realtime** | 36.46 | 138.63 | 36.40 | **150.73** |
| span_s | 1,432.8 | 376.8 | 3,108.8 | 750.8 |
| videos_per_s | 0.0195 | 0.0743 | 0.0193 | 0.0799 |
| chunks_per_s | 0.731 | 2.736 | 1.754 | 7.169 |
| frames_per_s | 2.432 | 9.249 | 2.410 | 9.981 |
| chunks_per_video | 37.4 | 36.8 | 90.9 | 89.7 |
| frames_per_video | 124.5 | 124.5 | 124.9 | 124.9 |
| realtime_streams | 36.5 | 138.6 | 36.4 | 150.7 |

(Chunks/video differ between runs because the 60-video set includes the
detection-dense IS/IB meetings; frames/video identical across arms in
both runs.)

## V2 — Latency (mode-labeled; not comparable across modes)

| metric | A: RR (c6) | A: LG (c6) | B: RR (blast) | B: LG (c60) |
|---|---|---|---|---|
| service p50 / p95 / p99 (s) | 302 / 361 / 365 | 75 / 94 / 94 | — (batch) | 359 / 437 / 447 |
| latency per footage-min (s) | 9.82 | 2.36 | — | 11.48 |
| batch span (s) | — | — | 3,108.8 (exact) | — |
| completion curve p50/p90/last (s) | — | — | 2,110 / 3,324 / 3,343 | — |
| time_to_first_result (s) | 159.8 | 39.1 | 215.8 | 172.3 |
| TTFR basis | per-request | per-request | batch event | per-request |
| failed_items | 0 | 0 | 0 | 0 |

Note the deliberate lesson in B's LG latency: at c60 every video queues
behind 59 others, so p50 rises to 359 s even though throughput is
maximal — the classic throughput-vs-latency trade, correctly labeled
instead of blended.

## V3 — Efficiency (the fairest cross-arm numbers)

| metric | A: RR | A: LG | B: RR | B: LG |
|---|---|---|---|---|
| **cpu_s_per_footage_min** | 10.47 | 9.46 | **10.42** | **10.88** |
| cpu_s_per_video | 325.4 | 294.1 | 327.4 | 341.9 |
| cpu_s_per_frame | 2.615 | 2.363 | 2.622 | 2.738 |
| cpu_s_per_detection | 0.566 | 0.513 | 0.260 | 0.273 |
| cpu_s_per_chunk | 8.704 | 7.988 | 3.603 | 3.812 |
| **effective_cores** (of 32) | **5.45** | **19.47** | **5.87** | **25.64** |
| scaling_efficiency | 0.17 | 0.61 | 0.18 | **0.80** |
| threads_activated | 998 | 225 | **3,804** | 1,960 |

(Per-detection/per-chunk differ between runs with the corpus mix —
that's why cpu_s_per_footage_min is the primary.)

## V5 — Cost

| metric | A: RR | A: LG | B: RR | B: LG |
|---|---|---|---|---|
| usd_per_1k_footage_hours | 39.17 | 10.30 | 39.23 | **9.47** |
| videos_per_day_per_box | 1,750 | 6,654 | 1,747 | 7,235 |

## V0 — Gates (identical verdicts both runs)

| gate | result | reading |
|---|---|---|
| census | PASS all four arm-runs (28/28, 60/60) | nothing lost, all failures would be named — there were none |
| structure | PASS all | 384-dim finite normalized throughout |
| self_duplication | PASS all | the RR double-emit bug class: absent |
| input_identity | PASS (28, 60) | both arms ate identical bytes |
| frame_parity | **PASS — frame counts identical on every common video in both runs** | the arms extract exactly the same frames |
| detection_ratio | PASS (all inside 0.90–1.10) | model-build drift ≤10% everywhere |
| chunk_ratio | PASS (all inside the 0.8–1.25 warn band) | equal work per video |
| workload_ratio | 1.02 (A), **1.013** (B) | equal total work |
| chunk_parity_tight | WARN, 3 videos (A) / 5 videos (B), all ±2–3 chunks | expected: different rfdetr builds |
| determinism | FAIL by design (single rep) | keeps these runs labeled sizing evidence |
| frame_law | FAIL on 2 (A) / 6 (B) videos — **identically on both arms** | a CORPUS finding, not an arm defect: ~10% of AMI meetings have video/audio stream-length mismatch (up to ~90 s); manifest durations come from audio. Fix: ffprobe video durations at staging |
| corpus_pin | SKIP | sha map lives in the corpus manifest but drivers don't copy it into the run manifest — two-line driver fix queued |
| metric_coverage | PASS all four arm-runs | every metric non-null or exempt |

## The findings, in one place

1. **Equal work, equal per-unit efficiency, unequal utilization.** Across
   both runs: workload ratio ≈ 1.01, per-footage-minute CPU within
   ~5–10% (winner alternates) — but LangGraph schedules 19.5→25.6 cores
   as backlog deepens while RocketRide holds 5.45–5.87 in every mode,
   scale, and configuration tested. The 3.8×→4.1× throughput and cost
   gaps are scheduling, not computation.
2. **RocketRide's ceiling is extraordinarily stable**: 36.2–36.8×
   realtime across five runs (blast/c6, 30/60 videos, EBS/RAM corpus,
   threads default/32). It is engine-bound, not workload- or
   client-bound. 3,804 threads spawned for 5.87 busy cores at 60-video
   blast.
3. **LangGraph scales with offered depth**: 19.5 cores at c6 → 25.6
   (80% of the box) at c60, per-unit cost flat. Its latency percentiles
   rise with queue depth exactly as theory predicts (75 s p50 at c6 →
   359 s at c60) — the throughput/latency trade the mode labels exist for.
4. **The corpus has a metadata defect the suite caught twice**: AMI A/V
   stream-length mismatch on ~10% of meetings, discovered by frame_law,
   proven benign by frame_parity.

## Caveats and what remains before results are quotable

Unpinned (LangGraph benefits from torch's freedom on idle cores — the
envelope will compress its advantage), single rep (determinism unproven
by construction), native modes differ by design (never compare the two
arms' latency numbers to each other — only within-arm across reps).
Remaining: envelope wiring (BENCH_CPUSET + OMP=1 + one deadline), ≥3
reps per arm/mode, seq baselines (activates the cross-mode block),
ffprobe video durations in the staged manifest, the corpus_pin driver
fix, full-corpus (170-video) staging & campaign.
