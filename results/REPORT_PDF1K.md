# PDF-1K concurrency benchmark — RocketRide vs LangGraph

> **EMULATED HARDWARE**: linux/amd64 images on an arm64 Apple M5 Pro host. Every timing below is relative-comparison-only; absolute figures are not portable. No native numbers exist yet.

Question: out-of-the-box defaults under a 1000-doc open-loop burst. NOT a scheduler-tuning comparison — no `threads=`, no `max_concurrency`, no `chunk_size` set anywhere.

## Setup (provenance: `runs/pdf1k/provenance.json`)

- containers: 12 CPU / 12 GB each (preregistered); host Apple M5 Pro, 18 cores / 24 GB; images run emulated
- corpus: 1000 Govdocs1 PDFs, manifest sha `7d7d90245a6b906e…`; ground truth 999/1000 extractable (offline pypdf 6.15.0)
- model: multi-qa-MiniLM-L6-cos-v1 @ HF rev `b20736733232`; sentence-transformers 5.7.0, torch 2.13.0
- LG dispatch (provenance only): async FastAPI endpoint; graph nodes are async functions calling sync workload code directly on the event loop; single uvicorn worker. No Send fanout: per-doc HTTP requests. Provenance only, not a claim.
- frozen params: {"formula": "max(60, calibration_p99 * 5) per arm \u2014 FROZEN before rep 1", "lg_calibration_p99_s": 60.96, "rr_calibration_p99_s": null, "lg_timeout_s": 304.8, "rr_timeout_s": 60.0, "container_cpus": 12, "container_memory_gb": 12, "submission": "open-loop burst, all docs at once, no client cap", "chunk_size": "NOT SET (framework defaults; pinned inequality stands)"}

## Reps

### cal-lg — VALID

  - records 100/100, ok 100; gates: returned=True unique=True dims=True finite=True norms=True gt_exact=True
  - batch span: 61.0 s; throughput 1.64 docs/s (successful-doc: 1.64)
  - TTFR: 27.34 s; batch-position latency p50/p90/p99: 35.2/43.3/61.0 s (includes queueing)
  - send window: 0.000103583 s

  - sampler: peak RSS 5348.7 MB, avg cores 17.93, peak threads 45 (891 samples @100ms)

### cal-rr — INVALID (watchdog)

  - records 0/100, ok 0; gates: returned=False unique=True dims=True finite=True norms=True gt_exact=None
  - metrics unavailable

  - sampler: peak RSS 5003.6 MB, avg cores 1.22, peak threads 529 (5844 samples @100ms)

### rep1-lg — INVALID (cutoff 06:40)

  - records None/None, ok None; gates: returned=None unique=None dims=None finite=None norms=None gt_exact=None
  - metrics unavailable

  - sampler: peak RSS 2342.5 MB, avg cores 1.03, peak threads 42 (1606 samples @100ms)


_Assembled by pdf1k/make_report.py; narrative sections are added by hand on top of this skeleton._
