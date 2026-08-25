# films500 — full-corpus native-mode sizing run (single rep, unpinned envelope)

Corpus: archive_films_v2 frozen 500 (manifest sha bd0c915e…4e02a1), 498 measured + 2 warm, 674.75 h footage (probed).

| arm | run (S3: s3://rocketride-benchmark-data/leela/videobench/) | posture | result |
|---|---|---|---|
| RocketRide 3.3.1 (patched) | `films500-20260824T073256Z/rr` | default: 1 task/token, blast, threads unset | 498/498, 35.0×, 6.88 cores, 11.79 cpu-s/fmin, $40.79/1k fh, span 19.27 h |
| LangGraph video-detect-v1 | `films500-20260825T061529Z/lg` | c32, one uvicorn, streaming-frames fix (commit 2d7533b) | 498/498, 154.6×, 25.46 cores, 9.88 cpu-s/fmin, $9.24/1k fh, span 4.36 h |

`report.txt` = the paired report (validity PASS, determinism NOT_RUN, evidence grade SIZING;
workload_ratio 1.026, input_identity 498, frame_parity VFR-band with 46 exact).

Provenance, preflight hashes, license classification, per-doc records, samplers: in the S3
bundles above (mirrored here under rr/ and lg/ where committed).

Read with two labels: (1) sizing evidence, not the publishable comparison (needs shared CPU
envelope + ≥3 reps); (2) RocketRide is in its DEFAULT single-task posture — the ~6-core
ceiling is per task (device lock), not per engine; the multi-task "parity" posture
(Ansh, WS-1, 2026-08-24) is a separate, pending leg. The first LangGraph attempt on this
corpus (`films500-20260824T073256Z/lg`) OOM-died at 97/498 — post-mortem in the 2d7533b commit.
