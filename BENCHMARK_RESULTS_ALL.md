# RocketRide vs LangGraph — Complete Results Ledger (AWS campaign, 14 Aug → 9 Sep 2026)

Every measured run of the AWS benchmark campaign, both tracks, in one place:
the **PDF track** (`aws_bench/`, GovDocs1 PDFs → parse → chunk → embed) and the
**video track** (`aws_videobench/`, AMI meetings and Internet Archive films → frames →
RF-DETR → chunk → embed). Compiled 9 September 2026 from every run bundle under
`s3://rocketride-benchmark-data/leela/` (`smoke/`, `blast/`, `rr/`, `bench/`,
`videobench/`) cross-checked against the committed results documents
(`BENCHMARK_RUNS.md`, `aws_bench/RESULTS_PDF.md`, `aws_videobench/RESULTS*.md`,
`aws_videobench/runs/`). Nothing here is new measurement; every number is re-derivable
from the raw records named beside it.

---

## At a glance

Two tracks, one box (`c7i.8xlarge`, 32 vCPU), RocketRide engine 3.3.1 with two documented
corrections, LangGraph as the comparison arm throughout. One row per question the campaign
answered; the grade column is the label that governs how far each row can be repeated
(§1 defines the grades). Every row links to the section holding the full table.

| question | RocketRide | LangGraph | reading | grade | where |
|---|---|---|---|---|---|
| PDF · 10,000 docs · 128 in flight · 32 cores | 49.9 chunks/s · 18.1 cores · p50 28.4 s | **121.0 chunks/s · 29.1 cores · p50 19.3 s** | LG 2.4× throughput; RR 1.5× the CPU per document; RR first result 1.0 s vs 2.1 s | **RUN PASS** | [§5.4](#54-series-2--32-core-envelope-full-host-18-aug) |
| PDF · RocketRide's best posture | 53.7 chunks/s at 17.5 cores (1k blast, 32 threads) | 121.0 (above) | still 2.3× behind; the ~15–18-core ceiling holds at 32 and 64 threads | SIZING | [§5.5](#55-rocketride-only-thread-probes-19-aug--1000-docs-blast-32-cores-skip_lg1) |
| PDF · one document at a time | p50 1.61 s · 8.70 CPU-s/doc | **p50 0.98 s · 2.56 CPU-s/doc** | 3.4× CPU gap uncontended; RR needs load to be efficient | SIZING (single rep) | [§5.4](#54-series-2--32-core-envelope-full-host-18-aug) |
| PDF · four poison files mid-batch | 0 collateral · **0/4 reported as errors** · +16–31 MB RSS | 0 collateral · 4/4 reported · +646–1,734 MB RSS | both isolate faults; RR fails silently and cleanly, LG loudly and memory-hungry | pass (fault gate) | [§5.3](#53-fault-injection-17-aug--runs-9-and-10) |
| PDF · repeatability | byte-identical ×3 reps · CV 0.09% | byte-identical ×3 reps · CV 0.81% | both deterministic, and byte-identical at c128 vs sequential | proven | [§5.2](#52-series-1--24-core-envelope-isolated-client-1617-aug), [§5.4](#54-series-2--32-core-envelope-full-host-18-aug) |
| PDF · PR #2197 length-sorted batching | +0.61% docs/s · −1.67% CPU-s/chunk (1 rep); +4.2% in the embed stage alone | — | positive and mechanism-consistent, unconfirmed | SIZING | [§5.6](#56-pr-2197-length-sorted-embedding-batching--ab-89-sep) |
| Video · AMI 168 meetings · each arm's default | 37.4× · 6.0 cores · $38.15 | **155.7× · 26.8 cores · $9.17** | 4.16× gap; CPU per footage-minute within 8% — a scheduling gap, not an efficiency gap | SIZING | [§6.2](#62-ami-default-posture-cross-arm-2122-aug--runs-a-b-c) |
| Video · AMI 168 · each arm's best posture | **16.21 f/s (243×) · 28.7 cores · $5.87** — 32 tasks × 1 thread | 14.71 f/s (221×) · 28.7 cores · $6.47 — 4 processes × c48 | RR +10.2%, at twice the memory (61 vs 30 GB) and 8.9 idle cores | SIZING | [§6.7](#67-rocketride-best-cells-on-ami-2-sep-and-the-ami-best-vs-best) |
| Video · AMI 168 · equal budget (6 inferences × 5 threads) | 12.73 f/s (191×) · 24.8 cores · 44.9 GB | 12.33 f/s (185×) · 20.9 cores · **14.7 GB** | RR +3.3%; LG 13% less CPU per frame with one model copy | SIZING | [§6.5](#65-matched-posture-on-ami-2526-aug) |
| Video · films 500 (675 h) · each arm's default | 35.0× · 6.9 cores · 19.3 h · $40.79 | **154.6× · 25.5 cores · 4.4 h · $9.24** | 4.4× gap; LG 16% cheaper per footage-minute at this scale | SIZING | [§6.4](#64-archive-films-default-posture-2225-aug) |
| Video · films 100 (145 h) · both arms tuned | 11.10 f/s (164×) · $8.71 — 16 tasks × 2 threads | **13.25 f/s (199×) · $7.18** — 8 processes × c48 | LG +19.4%; RR's frame extraction costs ~2.8× more on 1080p prints (spool hypothesis, unproven) | SIZING | [§6.8](#68-films-with-both-arms-tuned-35-sep) |
| Video · PR #2197 | +0.08% | — | inert: every chunk truncates to 512 tokens | SIZING | [§6.9](#69-pr-2197-video-ab-7-sep--pr2197-stockpatched-20260907t024121z) |

The one publishable-grade comparison is the PDF flagship (first row). Everything on the video
track is single-repetition on an unpinned box: consistent across many runs, but without error
bars. §7 sets the two tracks side by side; Appendix C lists adjacent work that is deliberately
not folded into these tables.

---

## Contents

0. [At a glance](#at-a-glance)
1. [How to read this document](#1-how-to-read-this-document)
2. [Environment](#2-environment)
3. [Timeline — every run, one line each](#3-timeline--every-run-one-line-each)
4. [Before AWS — the emulated Mac phase (context only)](#4-before-aws--the-emulated-mac-phase-context-only)
5. [PDF track (aws_bench)](#5-pdf-track-aws_bench)
   - 5.1 First runs on the box (14–15 Aug)
   - 5.2 Series 1 — 24-core envelope (16–17 Aug)
   - 5.3 Fault injection (17 Aug)
   - 5.4 Series 2 — 32-core envelope (18 Aug) — the RUN PASS flagship
   - 5.5 RocketRide-only thread probes (19 Aug)
   - 5.6 PR #2197 length-sorted batching A/B (8–9 Sep)
   - 5.7 Cross-cutting PDF results and findings
   - 5.8 Batch-submission campaign: 10k docs in batches of 16 / 32 / 64, both arms (15–21 Sep)
   - 5.9 Node-level attribution: the bottleneck is the parse node (21–22 Sep)
6. [Video track (aws_videobench)](#6-video-track-aws_videobench)
   - 6.1 Bring-up and sizing (19–20 Aug)
   - 6.2 AMI, default posture, cross-arm (21–22 Aug)
   - 6.3 RocketRide thread sweep (24 Aug)
   - 6.4 Archive films, default posture (22–25 Aug)
   - 6.5 Matched posture on AMI (25–26 Aug)
   - 6.6 LangGraph posture sweep and best cells on AMI (31 Aug – 2 Sep)
   - 6.7 RocketRide best cells on AMI (2 Sep) and the AMI best-vs-best
   - 6.8 Films with both arms tuned (3–5 Sep)
   - 6.9 PR #2197 video A/B (7 Sep)
   - 6.10 Cross-cutting video results and findings
   - 6.11 Node-level attribution on AMI, both arms: the bottleneck is the detect node, and it is a queue (22 Sep)
7. [Cross-track synthesis](#7-cross-track-synthesis)
8. [Appendix A — S3 run index](#appendix-a--s3-run-index)
9. [Appendix B — where the source documents live](#appendix-b--where-the-source-documents-live)
10. [Appendix C — adjacent work not folded in](#appendix-c--adjacent-work-not-folded-in)

---

## 1. How to read this document

**Evidence grades.** Every run carries one of these labels; the label matters more than
the number.

| grade | meaning | which runs |
|---|---|---|
| **RUN PASS** | every gate passed, including determinism (by repetition or by concurrency-invariance) — quotable under its stated caveats | PDF Run 11 only (`20260818T052912Z_c128`) |
| **SIZING** | single repetition and/or no CPU envelope — valid engineering signal, no error bars, not publishable as a comparison | every video run; PDF probes and A/Bs |
| **FAIL by design** | the fail-closed gate refused to certify a single-rep run; the numbers themselves are sound | PDF Runs 1–8, 12–13; early video runs |
| **pre-patch** | RocketRide chunk counts inflated ~40% by the duplication defect (before 16 Aug 23:47 UTC) — chunk-denominated RR metrics are flattered; CPU-per-document is honest | PDF Runs 4–8, the 14–15 Aug RR-only runs |
| **DIAGNOSTIC** | a harness or corpus gate tripped (frame_law calibration, worker_census readback) — numbers kept, not quotable | noted per run |

**Ingestion modes** (never compare latency across modes):

- `blast` — the whole backlog submitted at once; latency is *batch position* (queue wait
  included). RocketRide's native path is one `send_files` batch.
- `cN` — closed-loop, N requests in flight; latency is genuine *service latency*.
- `seq` — one at a time; uncontended service latency.
- `native_saturation` — each arm on its own path (LangGraph `c128`, RocketRide `blast`).
- `sharded_blast` — the matched RocketRide posture: K task processes each fed its own shard.

**Metric conventions.** Throughput is chunks/s (PDF) or frames/s and ×realtime (video).
Effective cores = cgroup `cpu.stat` CPU-seconds over the measured window ÷ window
length. CPU-per-document is the corruption-proof efficiency denominator (a framework
controls how many chunks it emits; it does not control the input). Video cost is
`$1.428/h ÷ x_realtime × 1000` per 1,000 footage-hours (c7i.8xlarge on-demand).
Warm-up documents are always disjoint from, and excluded from, the measured set.

---

## 2. Environment

| | |
|---|---|
| host | AWS EC2 `c7i.8xlarge` — 32 vCPU, 61–63 GB RAM, gp3 (100 GB, later 1 TB), Ubuntu 22.04, native x86_64, `i-0bdc8b1e18f2a5348`, us-east-1a, $1.428/h |
| RocketRide | engine **3.3.1** (`server-v3.3.1` linux-x64), SDK `rocketride` 1.3.0, in Docker, **with two documented corrections**: the Linux boot pin (`onnxruntime-gpu==1.20.1` → `1.20.2`, §5.7 finding 8) and the embedding-transformer duplication fix (`RR_DUP_PATCH=1`, §5.7 finding 1). The tested system is always "3.3.1 + two corrections", stamped in provenance and image labels — never stock 3.3.1. |
| LangGraph | FastAPI + compiled `StateGraph`, one arm per track (`aws_bench/arms/langgraph`, `aws_videobench/arms/langgraph`); PDF parse via an Apache Tika 3.2.3 sidecar carrying RocketRide's own `tika-config.xml`; video via ffmpeg `fps=1/15` + RF-DETR base (thr 0.3) |
| embedding | `multi-qa-MiniLM-L6-cos-v1`, 384-dim, L2-normalised, both arms (the engine's `miniLM` profile — not `all-MiniLM-L6-v2`) |
| chunking | PDF: 4000 chars / 200 overlap; video: 4000 / 0 over detection JSON |
| CPU accounting | cgroup v2 `cpu.stat usage_usec` sampled inside each container (host `/proc/stat` samplers were retired on 14 Aug — they counted idle) |
| memory accounting | PDF: cgroup `memory.stat anon`, contemporaneous sum across an arm's containers; video: cgroup `memory.current` max (includes page cache unless stated) |
| PDF envelope | Series 1: 24 cores per arm (`cpuset 0–23`), client isolated on `24–31`. Series 2: 32 cores, client unpinned (`client_isolated=false`). |
| video envelope | none in any run (all 32 vCPU, no cpuset, threads unpinned unless the posture sets BLAS/OMP vars); one arm at a time, other arm stopped |
| corpora | GovDocs1 PDFs, nested subsets (`bench_corpus_n{200,1000,10000}_off200`; `bench_corpus_200` at offset 0; `fault_corpus_200` + 4 poison). AMI meetings (`ami30h` 60×~30 min; `ami_full` 168 measured + 2 warm, 96.06 h probed / 98.19 h source metadata). Internet Archive films `archive_films_v2` (500 films, 677.2 h, 262 GiB, sealed sha `bd0c915e…`), nested subsets 10 ⊂ 50 ⊂ 100 ⊂ 500. |
| access model | SSM interactive shell only; code reaches the box by `git clone`; results leave by `aws s3 cp`; box idle-stops itself |

---

## 3. Timeline — every run, one line each

| date (UTC) | track | run id (S3 prefix) | what | headline |
|---|---|---|---|---|
| 14 Aug 19:13 | PDF | `smoke/20260814T191343Z` | first native-x86 measurement smoke, LG seq, 10 docs, pypdf | chain works: 2.15 docs/s, p50 0.49 s, M0 PASS |
| 14 Aug 21:06 | PDF | `blast/20260814T210610Z` | LG blast 150 docs × 3 reps, Tika, uncapped | 25.6–25.8 chunks/s, CV 0.4%, 4.0 cores |
| 14 Aug 22:49 → 15 Aug 02:01 | PDF | `rr/2026081{4,5}T*` (5) | RR-only 200-doc blasts: engine default vs `threads=32`, client pool 8/11/1 | ~16–17 chunks/s at ~6 cores regardless of knob; pool caps at 11 connections; pre-patch |
| 16 Aug 06:03 | PDF | `bench/20260816T060355Z_blast` (Run 8) | 200 docs blast × 3 reps, default threads | **determinism 198/198 ×3 both arms**; CV 0.81% / 0.09% |
| 16 Aug 07:27 / 07:42 | PDF | `…072711Z_c8` / `…074246Z_c12` (Runs 7, 6) | concurrency sweep | c8→c12 +19% throughput; RR pool "11 clients (1 failed)" |
| 16 Aug 17:39 | PDF | `…173915Z_blast` (Run 5) | RR `threads=24` on 24 cores | RR utilisation 22% → 63%; 24 threads becomes standard |
| 16 Aug 22:48 | PDF | `c128_1k_20260816T224851Z` (Run 4) | 1k, c128, stock 3.3.1 | **51/987 docs duplicated** — the before-patch baseline |
| 16 Aug 23:47 | PDF | `c128_1k_PATCHED_20260816T234710Z` (Run 3) | 1k, c128, patched | LG 103.5 vs RR 46.5 chunks/s; RR 15.5 cores of 24 |
| 17 Aug 02:31 | PDF | `1k_final_blast_run` (Run 2) | 1k native saturation, RR 32 threads on 24 cores | oversubscription buys nothing (46.15 chunks/s) |
| 17 Aug 04:15 | PDF | `10k_native_saturation` (Run 1) | **10,000 docs**, native saturation | LG 115.3 vs RR 46.6 chunks/s; per-unit numbers hold at 10× scale |
| 17 Aug 07:27 / 07:51 | PDF | `fault_…_c8` / `fault_…_seq` (Runs 9, 10) | 4 poison files, c8 and seq | zero blast radius both arms; RR reports poison as `complete` |
| 18 Aug 04:34 | PDF | `20260818T043359Z_c128` (Run 12) | 1k c128 on 32 cores | +33% cores → +3–6% throughput; RR 18.35 cores |
| 18 Aug 04:52 | PDF | `20260818T045211Z_seq` (Run 13) | 200 seq, invariance partner | LG p50 0.98 s / RR 1.61 s; RR 8.70 vs 2.56 CPU-s/doc |
| 18 Aug 05:29 | PDF | `20260818T052912Z_c128` (Run 11) | **10k c128 both arms, 32 cores** | **RUN PASS.** LG 121.0 vs RR 49.9 chunks/s; RR max latency 2,050 s on a 3-chunk doc |
| 19 Aug 02:32 | PDF | `20260819T023248Z` | RR `threads=256` | engine rejected: "Invalid thread count 256" (valid 1–64) |
| 19 Aug 02:41 / 15:25 | PDF | `20260819T024149Z` / `…152537Z` | RR 1k blast at 32 / 64 threads | 53.7 / 53.0 chunks/s, 17.5 / 17.7 cores — RR's best PDF throughput; ceiling confirmed |
| 19 Aug 19:16 | video | `videobench/smoke-20260819T191632Z` | ES2002a × 2 reps, dual-lane (Whisper + detect) pipe | detection byte-identical; **Whisper non-deterministic** across reps |
| 19 Aug 21:24 / 23:39 | video | `run30h-20260819T212432Z` / `…233913Z` | 60 × 30-min AMI, dual-lane | **ENOSPC at 47/60** — engine keeps a copy of every upload |
| 20 Aug 01:26 | video | `rundetect-20260820T012644Z` | 60 AMI, detect-only pipe | clean 60/60, 36.2×, **5.85 cores of 32** |
| 20 Aug 06:24 | video | `rundetect-20260820T062432Z` | same, `threads=32` | 5.59 cores, output byte-identical — knob inert |
| 20 Aug 05:42 | video | `capture-20260820T054250Z` | ES2016d frame/embedding capture | 102 frames == ffmpeg == duration/15; embeddings reproduce to 1e-7 |
| 20 Aug 18:44 | video | `s3test-20260820T184413Z` | corpus S3 → /dev/shm architecture | 30/30, 36.8×, hashes identical to EBS run |
| 21 Aug 00:01 | video | `lgsmoke-20260821T000140Z` | LangGraph video arm box smoke | 3 videos verified; decode ~55% / detect ~40% single-request |
| 21 Aug 19:53 | video | `h2h-20260821T195300Z` (Run A) | 28 AMI, both arms c6 | LG 138.6× vs RR 36.5×; **CPU per footage-min within 10%** |
| 21 Aug 21:08 | video | `native60-20260821T210828Z` (Run B) | 60 AMI, RR blast vs LG c60 | LG 150.7× / 25.6 cores vs RR 36.4× / 5.9 cores |
| 22 Aug 07:01 | video | `native170-20260822T070136Z` (Run C) | **full AMI 168**, RR blast vs LG c32, overnight | LG 155.7× / 26.8 cores vs RR 37.4× / 6.0 cores; 4.16× gap; corpus_pin first PASS |
| 22 Aug 19:07 | video | `archive10-20260822T190711Z` | 8 films, .mp4 shakedown | RR 41.9× (its best single-task); **frame_parity FAIL** — RR over-samples VFR prints |
| 23 Aug 17:05 / 17:56 | video | `films10-…170514Z` / `films50-…175555Z` | films default posture | RR 36–37× / 6 cores; LG 129–135× |
| 24 Aug 03:08 | video | `films10-20260824T030826Z` | films10 regression after pre-flight repair | marker-windowed CPU: RR 6.69 cores |
| 24 Aug 03:49 | video | `films500-20260824T034901Z` | films500 attempt 1 | LangGraph **OOM at 97/498** (all frames decoded to RAM) |
| 24 Aug 06:16 | video | `rrsweep-20260824T061609Z` | RR `threads` unset/8/32/64, 16 AMI | **dead flat**: 5.93–5.97 cores, 34.9–35.3× |
| 24 Aug 07:33 | video | `films500-20260824T073256Z` | films500 RR leg (LG leg died) | RR 498/498, 35.0×, 6.88 cores, 19.27 h |
| 25 Aug 06:00 / 06:15 | video | `films10-…060011Z` / `films500-20260825T061529Z` | LG reruns with streaming-frames fix | LG 498/498, 154.6×, 25.5 cores, 4.36 h; memory flat 9–14 GB |
| 25 Aug 14:47 / 17:07 | video | `matched8x4-ami-rep1-…144717Z` / `…170702Z` | matched-posture smoke attempts | aborted (harness bugs: census watcher hang; `ttl=0` guard) |
| 25 Aug 18:19 | video | `matched8x4-ami-rep1-20260825T181925Z` | matched smoke, N=8 | all gates incl. task/worker census PASS; RR 160× vs LG 185× (one video per process — null test) |
| 26 Aug 06:41 | video | `matched8x4-ami-rep1-20260826T064153Z` | **RR matched 8×4, full AMI** | 166.1×, 11.07 f/s, 23.9 cores — **4.5× over default** |
| 26 Aug 07:58 | video | `matched8x4-ami-rep1-20260826T075823Z` | LG `uvicorn --workers 8` one port | 100.7×, 11.3 cores; kernel skew 49/43/29/15/14/11/4/3 |
| 26 Aug 15:51 | video | `matched6x4-ami-rep1-20260826T155110Z` | RR 6 conn × `threads=32` | 177.2×, 21.4 cores, 1.81 CPU-s/frame |
| 26 Aug 17:21 | video | `matched6x5-ami-rep1-20260826T172137Z` | **equal-budget pair**: RR 6 tasks × 5 threads vs LG 1 process × 6 inferences × 5 threads | RR 191.0× / 24.8 cores vs LG 185.0× / 20.9 cores |
| 31 Aug 18:02 / 22:11 | video | `lgsweep-lg_*-20260831T*` (7 cells) | LG topology sweep, N=96 | 4 processes × c48 × torch 1 best (210×); torch 2 loses |
| 2 Sep 13:12 | video | `rrbest-16x2-ami-…` / `rrbest-32x1-ami-…` | RR best on full AMI | **32×1 = 243.2×, 16.21 f/s, 28.7 cores** |
| 2 Sep 17:40 / 19:59 | video | `lgbest-lg_{4proc_c48,8proc_c32,4proc_c64}_t1-…` | LG best on full AMI | **4×c48 = 220.7×, 14.71 f/s, 28.7 cores** |
| 3 Sep 04:08 | video | `filmsbest-50-20260903T040810Z` | AMI optima applied to films50 | LG 151.6× beats RR 131.8× — optimum is corpus-dependent |
| 3 Sep 04:08 | video | `filmsbest-500-20260903T040810Z` | films500 at best configs | stopped by request at ~40 films — no numbers |
| 3 Sep 20:26 | video | `rrfilms-16x2-…` / `rrfilms-8x4-…` | RR posture sweep on films50 | 16×2 = 153.6× beats 32×1 by 16.5%; 8×4 worse |
| 5 Sep 02:33 | video | `films100-{rr16x2,lg4x48t1,lg8x48t1,lg4x48t2}-…` | films100, both arms tuned, cache-prewarmed | **LG 8×c48 = 198.8× vs RR 16×2 = 163.9× (+19.4%)** |
| 7 Sep 02:41 | video | `pr2197-{stock,patched}-20260907T024121Z` | PR #2197 A/B, AMI 168, RR default | +0.08% — inert (512-token truncation) |
| 8 Sep 22:38 / 22:48 | PDF | `20260908T223816Z` / `…224842Z` | PR #2197 A/B, 1k PDFs, RR default | +0.61% docs/s, **−1.67% CPU-s/chunk** |
| 9 Sep 07:18 | PDF | `micro2197-20260909T071851Z` | `encode()` microbench inside the engine runtime | +4.2% embed stage on real chunks; vectors identical |
| 16 Sep 06:59 | PDF | `batch10k-2026-09-15/batch10k-b16-20260916T065635Z` | **10k docs in `send_files` batches of 16**, one connection, standard engine, RR only | **RUN PASS.** 11.64 chunks/s at 3.79 cores (16% of 24); 6.98 CPU-s/doc; the 3-chunk tail docs take 725–1,677 s of *work* |
| 16 Sep 12:06 | PDF | `…/batch10k-b32-20260916T065635Z` | same, batches of 32 | **RUN PASS.** 14.09 chunks/s at 4.63 cores; 7.06 CPU-s/doc |
| 16 Sep 16:19 | PDF | `…/batch10k-b64-20260916T065635Z` | same, batches of 64 | **RUN PASS.** 17.24 chunks/s at 5.64 cores; 7.02 CPU-s/doc; all three cells byte-identical |
| 21 Sep 07:54 | PDF | `batch10k-lg-2026-09-21/batch10k-lg-b{16,32,64}-20260921T075403Z` | **the same three batch sizes on LangGraph**, waves joined at a barrier | **RUN PASS ×3.** 25.5 / 32.7 / 41.8 chunks/s; 2.40 / 2.66 / 2.89 CPU-s/doc; worst document 217 s vs RocketRide's 1,677 s |
| 21 Sep 13:36 | PDF | `…/batch10k-paired-b{16,32,64}-20260921T075403Z` | paired cross-arm reports, assembled from copies | 8,504 / 9,913 byte-identical at every batch size — the same 1,409-document transposition set as Runs 1 and 11 |
| 22 Sep 02:27 | PDF | `nodeprofile-2026-09-21/nodeprofile-{traced,control}-smoke20260922T022724Z` | per-node tracing switched on for the first time, plus its perturbation control | tracing costs 0.5%; **parse 10.2× LangGraph, embed 1.00×** on identical documents |
| 22 Sep 02:44 | PDF | `nodeprofile-2026-09-21/pathology-20260922T024429Z` | six documents sequentially, per-document node attribution | **99.98% of the worst document's 1,565 s is the parse node**; 3.01 MB control parses in 2.4 s |
| 22 Sep 21:48 | video | `nodeprofile-ami-2026-09-22/nodeprofile-ami-{rr-traced,lg,rr-seq,lg-seq}-20260922T214831Z` | node-level attribution on AMI, both arms, 24 videos batched + 6 sequential | **detect is 95% of RocketRide's node time and a queue**: CPU/frame matches LangGraph within 9%, but detect wall/frame inflates 20× under load for +10% throughput; embedding within 22% |

---

## 4. Before AWS — the emulated Mac phase (context only)

From 4–13 August the harness was built and exercised on an Apple M5 Pro running
linux/amd64 images under Rosetta (`emulated: true` in every provenance record). Those
runs are **not quotable** and are excluded from every table below; they exist in the
repo as `results200/RESULTS_SEQ200.md` (sequential 200 PDFs: RocketRide wedged at
`000744.pdf`, 140/175 completed), `results/REPORT_PDF1K.md` (1k open-loop burst,
mostly invalid reps), `results500/REPORT_PDF500.md` (500-doc burst: 0/500 on both arms
for opposite reasons — RR engine livelock vs frozen client timeout), and
`gate50/GATE50_REPORT.json` (the first native-macOS RocketRide 3.3.1 gate run, 49/50
both arms). Their one durable contribution: the per-document empty-result failures
(`000164`, `000357`) reproduced identically on native x86, so they are corpus
properties; the wedge/livelock never reproduced natively and stays unattributed.
The still-older `~/Desktop/RR_BENCH` harness (engine 3.2.1, also emulated) is
superseded entirely.

For the record, the four emulated PDF runs and what each left behind:

| record | what it was | LangGraph | RocketRide | durable takeaway |
|---|---|---|---|---|
| `gate50/GATE50_REPORT.json` (RR 3.3.1 native macOS vs LG in Docker, 50 docs, blast, 8-connection pool) | first gate run on engine 3.3.1 | 50/50 · all gates PASS · 1.158 docs/s over the 30-doc window · peak RSS 7.3 GB | 49/50 · all gates PASS · 0.839 docs/s · peak RSS 3.1 GB | the gate chain (census, structure, determinism) works end to end; `000164.pdf` empty on LG; cross-arm chunk delta median 0, char ratio 0.992 |
| `results200/RESULTS_SEQ200.md` (200 docs one at a time; LG 7 Aug on 2 CPU, RR 10 Aug on 12 CPU — not matched) | uncontended latency and work parity | 200/200 · 0.321 docs/s · p50 1.46 / p99 34.2 s | 140 of 175 attempted · 0.158 docs/s · p50 1.83 / p99 141.8 s · pipe wedged at `000744.pdf`, 33 timeouts after | work parity on real GovDocs: chunk-count delta median 0, char ratio 0.994; `000164` / `000357` empty on both arms; wedging is state-dependent, not per-document |
| `results/REPORT_PDF1K.md` (1,000 docs, open-loop burst, 12 CPU / 12 GB per arm) | out-of-the-box burst | calibration rep valid (100/100, 1.64 docs/s); measured rep cut off | calibration rep invalid (watchdog, 0/100) | no usable comparison; predictions were pre-registered (`PREDICTIONS_PDF1K.md`) |
| `results500/REPORT_PDF500.md` (500 docs, open-loop burst, 12 CPU / 10 GB) | capacity census | 0/500 by protocol — the frozen 107.6 s client timeout was shorter than the queue; the server itself completed ~61 | 0/500 — the engine livelocked twice at ~18 average cores with zero output | the wedge / livelock signature never reproduced on native x86; attribution (emulation vs product) left open |

---

## 5. PDF track (aws_bench)

Pipeline under test, both arms: `webhook → parse (Tika) → preprocessor_langchain (4000/200)
→ embedding_transformer (miniLM 384-d) → response_documents`. Pipeline contract
fingerprint `pipe_components_sha256 = 936e4a3997674d52…`, identical across every run.

### 5.1 First runs on the box (14–15 Aug) — pre-envelope, pre-patch

These established that the measurement chain worked natively and produced the first
RocketRide numbers. No cpuset, engine at 3.3.1 with the boot-pin fix only (the
duplication defect was not yet known), corpus = first 200 GovDocs1 files.

| run | arm / config | ok | chunks | chunks/s | docs/s | span s | latency (batch-position) | eff. cores | CPU-s/doc | peak RSS | threads |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `smoke/20260814T191343Z` | LG seq, 10 docs, pypdf | 10/10 | — | — | 2.145 | 4.66 | p50 0.489 s (service) | 0.54 | 0.46 | 0.6 GB | 5 |
| `blast/20260814T210610Z` rep1 | LG blast, 150 docs, Tika | 148/150 | 2,642 | 25.81 | 1.446 | 102.4 | p50 15.1 / p95 28.4 | 4.01 | 2.78 | 7.6 GB | 57 |
| ″ rep2 | ″ | 148/150 | 2,642 | 25.62 | 1.435 | 103.1 | p50 10.8 / p95 27.3 | 4.00 | 2.78 | 9.1 GB | 56 |
| ″ rep3 | ″ | 148/150 | 2,642 | 25.61 | 1.435 | 103.2 | p50 10.3 / p95 27.4 | 3.96 | 2.77 | 8.7 GB | 67 |
| `rr/20260814T224936Z` | RR blast, 200 docs, engine-default threads, client pool 8 | 198/200 | 4,577* | 15.75 | 0.681 | 290.7 | p50 11.2 / p99 154.7 | 5.93 | 8.71 | 10.5 GB | 399 |
| `rr/20260815T010908Z` | ″, pool 11 | 198/200 | 4,577* | 17.03 | 0.737 | 268.8 | p50 10.5 / p99 126.9 | 5.81 | 7.89 | 10.2 GB | 372 |
| `rr/20260815T012817Z` | RR blast, `threads=32`, pool 11 | 198/200 | 4,577* | 16.02 | 0.693 | 285.7 | p50 10.0 / p99 145.5 | 5.98 | 8.62 | 9.6 GB | 271 |
| `rr/20260815T015052Z` | re-report of the 012817Z shot (identical driver records, different sampler capture) | ″ | ″ | ″ | ″ | ″ | ″ | 5.75 | 9.04 | 9.1 GB | 273 |
| `rr/20260815T020130Z` | RR blast, `threads=32`, pool 1 (single connection) | 198/200 | 4,577* | 16.01 | 0.693 | 285.8 | p50 11.0 / p99 147.7 | 5.97 | 8.62 | 8.6 GB | 268 |

\* pre-patch: 4,577 chunks on a corpus where LangGraph produces 3,183 (Run 8, same 200
files) — the first sighting of the 1.44× duplication inflation, not yet recognised.

What it settled: (1) the LangGraph blast is repeatable to 0.4%; (2) RocketRide's
engine-default posture holds ~6 cores of 32 whatever the client does — pool 8, 11 or 1,
threads default or 32; (3) the client pool silently caps at **11 connections**
(`client_pool: 11`, `client_pool_cap: null`), the first appearance of the connection
funnel; (4) both arms fail the same two documents (`000164`, `000357`) with clean
empty results — corpus property.

### 5.2 Series 1 — 24-core envelope, isolated client (16–17 Aug)

Both arms on `cpuset 0–23`, client containerised on `24–31`, Tika for both, warm-up
disjoint. Run numbering follows `BENCHMARK_RUNS.md`. "bp" = batch-position latency;
"svc" = service latency. Chunk counts and chunk-denominated RR metrics in *italics* are
pre-patch (inflated).

| run | S3 id | mode · docs · RR threads | arm | ok | chunks | chunks/s | docs/s | span s | p50 / p95 / p99 (s) | TTFR s | eff. cores (util) | CPU-s/doc | CPU-s/chunk | peak RSS | threads |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **8** | `20260816T060355Z_blast` | blast · 200 (offset 0) · default · **3 reps** | LG | 198/200 | 3,183 | 36.87 (median) | 2.29 | 86.3 | 8.3 / 16.1 / 28.6 bp | 1.3–2.2 | 4.64–5.08 (19–21%) | 2.01–2.23 | 0.125–0.139 | 10.5 GB | 59–65 |
| | | | RR | 198/200 | *4,577* | *17.18* | 0.743 | 266.4 | withheld (no per-doc timestamps yet) | 2.7–2.9 | 5.28–5.30 (22%) | 7.11–7.14 | *0.307* | 9.4–9.9 GB | 355–383 |
| **7** | `20260816T072711Z_c8` | c8 · 200 · default | LG | 199/200 | 4,186 | 47.63 | 2.264 | 87.9 | 1.10 / 10.5 / 31.2 svc | 0.54 | 5.98 (24.8%) | 2.63 | 0.1251 | 6.4 GB | 20 |
| | | | RR | 199/200 | *5,962* | *47.70* | 1.592 | 125.0 | 1.85 / 18.3 / 34.5 svc | 0.66 | 8.08 (33.7%) | 5.07 | *0.1693* | 6.1 GB | 289 |
| **6** | `20260816T074246Z_c12` | c12 · 200 · default | LG | 199/200 | 4,186 | 56.77 | 2.699 | 73.7 | 1.28 / 12.1 / 32.1 svc | 0.72 | 8.06 (33.5%) | 2.98 | 0.1416 | 7.3 GB | 24 |
| | | | RR | 199/200 | *5,962* | *58.98* | 1.969 | 101.1 | 1.96 / 20.4 / 35.9 svc | 0.76 | 10.66 (44.4%) | 5.41 | *0.1806* | 7.7 GB | 304 |
| **5** | `20260816T173915Z_blast` | blast · 200 · **24** | LG | 199/200 | 4,186 | 73.48 | 3.493 | 57.0 | 19.6 / 32.3 / 54.4 bp | 10.87 | 13.55 (56.5%) | 3.89 | 0.1847 | 12.4 GB | 58 |
| | | | RR | 199/200 | *5,962* | *72.29* | 2.413 | 82.5 | withheld | 0.74 (upload-ack) | 15.04 (62.7%) | 6.24 | *0.2082* | 9.6 GB | 255 |
| **4** | `c128_1k_20260816T224851Z` | c128 · 1,000 · 24 · **stock 3.3.1** | LG | 987/1000 | 17,622 | 103.94 | 5.822 | 169.5 | 17.0 / 33.1 / 58.1 svc | 6.08 | 20.45 (85.1%) | 3.51 | 0.1965 | 12.8 GB | 53 |
| | | | RR | 987/1000 | *24,723* (51 docs ×2) | *63.52* | 2.536 | 389.2 | 28.8 / 49.8 / 79.1 svc, max 322 | 0.62 | 15.31 (63.8%) | 6.04 | *0.2410* | 12.5 GB | 248 |
| **3** | `c128_1k_PATCHED_20260816T234710Z` | c128 · 1,000 · 24 · **patched** | LG | 987/1000 | 17,622 | 103.48 | 5.796 | 170.3 | 16.8 / 32.6 / 56.1 svc, max 121 | 5.88 | 20.38 (84.9%) | 3.52 | 0.1970 | 12.4 GB | 53 |
| | | | RR | 987/1000 | 17,615 | 46.45 | 2.603 | 379.2 | 28.0 / 48.2 / 77.7 svc, max 313 | 0.65 | 15.46 (64.4%) | 5.94 | 0.3329 | 10.4 GB | 279 |
| **2** | `1k_final_blast_run` (= `20260817T023134Z`) | native sat · 1,000 · **32 on 24 cores** | LG c128 | 987/1000 | 17,622 | 113.09 | 6.334 | 155.8 | 15.9 / 30.2 / 52.0 svc | 5.61 | 20.28 (84.5%) | 3.20 | 0.1793 | 12.6 GB | 52 |
| | | | RR blast | 987/1000 | 17,615 | 46.15 | 2.586 | 381.7 | 113 / 206 / 216 bp | 1.05 (upload-ack) | 14.45 (60.2%) | 5.59 | 0.3132 | 11.6 GB | 290 |
| **1** | `10k_native_saturation` (= `20260817T041548Z`) | native sat · **10,000** · 24 | LG c128 | 9,913/10,000 | 212,858 | **115.31** | 5.370 | 1,846 (30.8 min) | 20.5 / 37.8 / 71.0 svc, max 524 | 4.35 | 22.35 (93.1%) | 4.16 | 0.1938 | 17.1 GB | 51 |
| | | | RR blast | 9,913/10,000 | 212,828 | **46.60** | 2.171 | 4,567 (76.1 min) | 1,460 / 2,776 / 2,897 bp | 1.63 (upload-ack) | 14.61 (60.9%) | 6.73 | 0.3134 | 14.3 GB | 288 |

Notes per run:

- **Run 8** is the only multi-rep PDF run: every metric STABLE, both arms
  **byte-identical across all three repetitions (198/198, zero mismatches)**. CV on
  chunks/s 0.81% (LG) / 0.09% (RR). Absolute throughput is the campaign's lowest
  (pre-pinning, pre-patch, pre-containerised client) — its job was variance.
- **Runs 7/6**: 8 → 12 in flight bought ~19% throughput for ~16% worse p50 on both arms;
  neither near saturation. Run 6 exposed the ceiling in the log: `pool: 11 clients
  (1 failed: ConnectionError)`. Both arms failed exactly one document, `001657.pdf`.
- **Run 5**: pinning RR to 24 engine threads lifted its utilisation from 22% (Run 8) to
  62.7%; the near-equal chunks/s is the duplication defect inflating RR's numerator.
- **Run 4 → 3 (the before/after)**: removing 28.8% of RR's output (24,723 → 17,615
  chunks) cost **1.6% less CPU** (5,959 → 5,864 CPU-s) — the duplicates were nearly
  free, so per-chunk cost had been flattered by 38%; peak RSS fell 17%. LangGraph moved
  < 3.4% across the pair (the control).
- **Run 2**: oversubscribing RR (32 threads on 24 cores) changed nothing — throughput
  flat, utilisation *down* (60.2% from 64.4%). The idle cores are not I/O wait.
- **Run 1**: per-unit numbers held at 10× scale (chunks/s within 2% of 1k on both
  arms), killing the "RR amortises at scale" hypothesis. Cross-arm: 8,504 / 9,913
  byte-identical, 1,409 differ (the parser transposition set, §5.7).

### 5.3 Fault injection (17 Aug) — Runs 9 and 10

`fault_20260817T072750Z_c8` and `fault_20260817T075108Z_seq`: 200 clean documents plus
four poison files at positions 2 / 67 / 134 / 199 (`corrupt` 64 KB noise with a `%PDF`
header, `zero_byte`, `truncated` xref, `oversized_garbage` 5 MB), 24 cores, RR 24
threads, warm 0, timeout 900 s. Method: pre-fault baseline → fault corpus → recovery
probe (a clean document never in the corpus) → diagnostics before any restart.

| check | LangGraph | RocketRide |
|---|---|---|
| census | 199/204 completed in both modes; 4 poison + `001657.pdf` failed | same 199/204 |
| **poison surfaced by the server** | **4/4** — `http_500 processing_failed` ×3, `http_400 empty_input` ×1 | **0/4** — every poison returned `action: "complete"` with an objectId, `pdf:ocrPageCount: "0"` and an empty document list; the zero-byte file reported `file_size: 0` and still "completed" |
| blast radius (distinct collateral) | seq: 0. c8: raw report lists 3 "collateral" records, all `183_001657.pdf` — the known text-free document, an independent failure | same: 0 distinct in both modes |
| restart required | no | no |
| recovery probe | 32 chunks, 384-dim, real text | 32 chunks, 384-dim, real text |
| RSS growth around the fault (seq / c8) | 646 / 1,734 MB | **16.5 / 31.4 MB** |
| data isolation vs clean baseline | 199/199 byte-identical (`BENCHMARK_RUNS.md`) | 199/199 byte-identical |

Caveats recorded honestly: the raw `fault_report.txt` in S3 carries
`data_isolation: PASS null — no baseline supplied, UNVERIFIED` (the 199/199 figure was
established by comparing against the clean runs afterwards, outside the fault script),
and the c8 report's `PASS_zero_blast` reads `false` because it counted `001657` before the
"exclude independent failures" logic existed (the seq report, generated later, has it
and reads `true`). Neither framework was damaged by any poison in either mode; they fail
*differently* — LangGraph loud and memory-hungry, RocketRide silent and memory-clean.
The native-saturation fault run (poison inside a single RR batch) was never executed.

### 5.4 Series 2 — 32-core envelope, full host (18 Aug)

Both arms on all 32 cores; RR at 32 threads; client unpinned and disclosed as
`client_isolated=false`. Correctness chain finalised before launch (`dedbc47`, `34397ce`,
`fe58b98`): census `report` policy for text-free documents, cross-arm byte parity
reported but not gated, determinism by concurrency-invariance wired, pipe fingerprint and
honest `timeout_s` in provenance.

| run | S3 id | mode · docs | arm | ok | chunks | chunks/s | docs/s | span s | p50 / p90 / p95 / p99 / max (svc) | TTFR s | conc. achieved | eff. cores (util) | CPU-s/doc | CPU-s/chunk | peak RSS | threads | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **11** | `20260818T052912Z_c128` | c128 · 10,000 | LG | 9,913/10,000 | 212,858 | **120.97** | 5.634 | 1,759.6 (29.3 min) | 19.3 / 29.6 / 36.4 / 69.4 / 530.7 | 2.11 | 123.8 of 128 | **29.10 (90.9%)** | 5.17 | 0.2405 | 17.7 GB | 51 | **M0 PASS** |
| | | | RR | 9,913/10,000 | 212,828 | **49.85** | 2.322 | 4,269.0 (71.2 min) | 28.4 / 43.2 / 53.5 / 110.2 / **2,049.7** | **1.02** | 77.3 of 128 | **18.14 (56.7%)** | 7.81 | 0.3639 | 14.9 GB | 299 | **M0 PASS** |
| **12** | `20260818T043359Z_c128` | c128 · 1,000 | LG | 987/1000 | 17,622 | 109.23 | 6.118 | 161.3 | 15.8 / 24.0 / 31.9 / 55.9 / 115.5 | 5.34 | 107.2 | 25.99 (81.2%) | 4.25 | 0.2377 | 13.1 GB | 53 | FAIL* |
| | | | RR | 987/1000 | 17,615 | 47.93 | 2.686 | 367.5 | 23.5 / 35.4 / 47.6 / 81.6 / 310.4 | 0.68 | 70.3 | 18.35 (57.3%) | 6.83 | 0.3826 | 13.4 GB | 324 | FAIL* |
| **13** | `20260818T045211Z_seq` | seq · 200 (warm 2) | LG | 199/200 | 4,186 | 8.72 | 0.415 | 480.0 | **0.976** / 5.77 / 9.99 / 29.6 / 31.9 | — | 1.0 | 1.06 | **2.56** | 0.1215 | 3.6 GB | 12 | FAIL* |
| | | | RR | 199/200 | 4,187 | 5.62 | 0.267 | 745.4 | 1.613 / 10.6 / 17.3 / 31.6 / 34.0 | — | 1.0 | **2.32** | **8.70** | 0.4133 | 3.1 GB | 215 | FAIL* |

\* Runs 12 and 13 were generated before the invariance wiring; the evidence that flips
them exists (`CROSS_MODE_DETERMINISM.json` in Run 12's directory). Run 11's report was
regenerated with it and reads PASS on both arms with basis
`concurrency-invariance (c128 vs seq, 199/199 identical)`. The original pre-regeneration
copy is also in S3 as `20260818T052912Z` (identical numbers, verdict FAIL).

**Run 11 — the tail, by actual documents.** LangGraph's slowest documents are its
biggest (wait proportional to own work: `012852.pdf` 530.7 s for 1,959 chunks);
RocketRide's slowest are tiny (`011464.pdf` **2,049.7 s for 3 chunks**, `039660.pdf`
1,882.5 s for 3 chunks). 16 RR documents exceeded 300 s (7 over 600 s); LG had 3 over
300 s and none over 600 s. Head-of-line blocking through the ~11-connection funnel; the
tail grew from 310 s at 1k to 2,050 s at 10k, so it is unbounded in backlog.

**Run 12 — what 33% more cores bought.** LG +5.6% throughput (103.5 → 109.2 chunks/s),
RR +3.2% (46.5 → 47.9), at +21% / +15% worse CPU per chunk. RR converted 2.9 of the 8
extra cores (15.46 → 18.35); LG converted 5.6 (20.38 → 25.99). The 24-core envelope remains the efficiency point.

**Run 13 — uncontended.** LangGraph answers in under a second at the median, RocketRide
in 1.6 s; tails converge at p99 (the documents themselves). The sharpest efficiency
finding of the campaign: **RR burns 3.4× LangGraph's CPU per document with one in
flight** (8.70 vs 2.56 CPU-s/doc, holding 2.3 cores busy for serial work), versus
1.5–1.7× under load. Matches Shashi's independent sequential numbers (RR 4.91–9.58 vs
Haystack 1.45–3.33).

**Concurrency invariance (Runs 11/12 × 13).** Ordered chunk digests per document,
C=128 vs sequential, per arm: Run 12 vs 13 → LG 199/199, RR 199/199; Run 11 vs 13 → LG
199/199, RR 199/199. **Both frameworks produce byte-identical output whether 128
requests are in flight or one.** The check was validated first: pointed at the pre-patch
pair it flags exactly the 51 duplicated RR documents and reads LG 987/987 clean.

### 5.5 RocketRide-only thread probes (19 Aug) — 1,000 docs, blast, 32 cores, `SKIP_LG=1`

| S3 id | `threads=` | ok | chunks | chunks/s | docs/s | span s | eff. cores (util) | CPU-s/doc | CPU-s/chunk | peak RSS | OS threads | reading |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `20260819T023248Z` | **256** | **0/1000** | 0 | 0 | 0 | 0.06 | (idle noise) | — | — | 0.7 GB | 117 | **engine rejected it**: every `send_files` returned `action:"error"`, `"Invalid thread count 256"`, task never started. Valid range is 1–64 (`scan.cpp:493`; confirmed by the engine team on #2053). Not a silent disable — the "second manifestation" paragraph in issue #2052 was wrong and is retracted. |
| `20260819T024149Z` | 32 (control) | 987/1000 | 17,615 | **53.68** | 3.008 | 328.1 | 17.48 (54.6%) | 5.81 | 0.3257 | 13.9 GB | 292 | RocketRide's best PDF throughput of the campaign |
| `20260819T152537Z` | 64 | 987/1000 | 17,615 | 53.03 | 2.972 | 332.2 | 17.69 (55.3%) | 5.95 | 0.3336 | 13.4 GB | 382 | +31% OS threads, +0.2 effective cores — the ceiling is the dispatcher, not the pool |

Provenance in these three is incomplete by design of the probe (`parser_config_hash`,
`chunk_config`, `embedding_model` missing) — sizing grade.

### 5.6 PR #2197 length-sorted embedding batching — A/B (8–9 Sep)

Stepan's `rocketride-server` PR #2197 sorts sentences by length before batching in
`_encode_local` (batch 32) and restores order afterwards. Applied to the 3.3.1 image as
`patches/lensort_2197.py` behind build arg `RR_LENSORT_PATCH`, image label
`benchmark.rocketride.lensort_patch_pr2197`; the patched image was verified three ways
(digest, label, source grep at lines 383/387/425). Sort/restore verified with 2,000
randomised trials.

**PDF A/B** — GovDocs1 slice, N = 1,000 + 25 warm from `bench_corpus_n10000_off200`,
blast, RR default posture (`threads` unset, 1 task), 24-CPU envelope, 1 rep each, git
`6803a36` dirty:

| metric | stock `20260908T223816Z` | patched `20260908T224842Z` | delta |
|---|---|---|---|
| ok / chunks | 988/1000 (12 empty-extraction, same set) / 18,801 | 988/1000 / 18,801 | identical work |
| docs/s | 2.0225 | **2.0348** | **+0.61%** |
| chunks/s | 38.49 | **38.72** | +0.61% |
| span | 488.5 s | 485.6 s | −0.6% |
| **CPU-s per chunk** | 0.3544 | **0.3485** | **−1.67%** |
| CPU-s per doc | 6.744 | 6.632 | −1.66% |
| p95 latency (batch-position) | 252.7 s | 248.0 s | −1.9% |
| effective cores (util) | 13.64 (56.8%) | 13.50 (56.2%) | −1.1% (mechanism, not a win) |
| peak RSS / threads | 11.9 GB / 425 | 12.3 GB / 420 | — |
| self-duplication | 0/988 | 0/988 | — |
| M0 | FAIL (1 rep; census `fail` policy; provenance fields) | same, symmetric | comparison valid; numbers SIZING |

Throughput up while CPU down is the signature of reclaimed padding, not drift. Corpus
justification: GovDocs chunks per document p50 6 / p90 36 / max 276; 10.7% of documents
exceed the 32-chunk batch but they hold 57.6% of all chunks. Campaign calibration puts RR
default-posture repeatability at ~0.12%, so +0.61% is above noise but a single
observation. Two harness bugs found (pre-build in-image verification greps the previous
cell's image; auto-summary regex prints empty) are unfixed. All A/B code is
**uncommitted** on `aws-bench`.

**`encode()` microbench** (`micro2197-20260909T071851Z`, engine's own embedded Python,
`torch_threads=1`, stock = same image with the one file reverted, variant proven from
loaded bytecode; inputs = the engine's real chunks reproduced via Tika 3.2.3 +
`RecursiveCharacterTextSplitter()` — 981/988 documents with identical chunk counts):

| input | stock wall s | patched wall s | gain |
|---|---|---|---|
| N=16 chunks | 2.133 | 2.100 | +1.6% |
| N=64 | 8.708 | 8.601 | +1.2% |
| N=256 | 34.47 | 33.69 | +2.3% |
| N=1024 | 138.57 | 131.22 | **+5.6%** |
| equal-length control, 64 | 8.597 | 8.625 | −0.3% (Stepan predicts −1%) |
| **per-document replay, 988 docs / 18,807 chunks** | 2,402.9 | 2,306.5 | **+4.18%** |
| — 135 docs with > 32 chunks (11,952 chunks) | 1,560.9 | 1,468.8 | +6.27% |
| — 853 docs with ≤ 32 chunks (6,855 chunks) | 842.0 | 837.7 | +0.51% |

Vectors identical: 1,822 compared, max |diff| 1.5e-7, 0 misaligned. Stepan's claimed
+30/+70/+87% is for genuinely mixed lengths; our chunks (p50 3.8k chars) mostly truncate
to 512 tokens, which equalises them. Embed share of pipeline CPU ≈ 36% → predicted
end-to-end ≈ +1.5%, consistent with the measured −1.67% CPU-s/chunk. An earlier attempt
`micro2197-20260909T071314Z` has logs only (aborted before results).

### 5.7 Cross-cutting PDF results and findings

**Per-unit cost — all loaded runs, two denominators** (successful documents, rep 1):

| run | envelope | LG CPU-s/doc | RR CPU-s/doc | ratio | LG CPU-s/chunk | RR CPU-s/chunk | ratio |
|---|---|---|---|---|---|---|---|
| 11 · 10k c128 | 32c | 5.17 | 7.81 | **1.51×** | 0.2405 | 0.3639 | 1.51× |
| 12 · 1k c128 | 32c | 4.25 | 6.83 | 1.61× | 0.2377 | 0.3826 | 1.61× |
| 13 · 200 seq | 32c | 2.56 | 8.70 | **3.40×** | 0.1215 | 0.4133 | 3.40× |
| 1 · 10k native | 24c | 4.16 | 6.73 | 1.62× | 0.1938 | 0.3134 | 1.62× |
| 2 · 1k native | 24c | 3.20 | 5.59 | 1.75× | 0.1793 | 0.3132 | 1.75× |
| 3 · 1k c128 | 24c | 3.52 | 5.94 | 1.69× | 0.1970 | 0.3329 | 1.69× |
| 4 · 1k c128 pre-patch | 24c | 3.51 | 6.04 | 1.72× | 0.1965 | *0.2410* | *1.23×* |
| 5 · 200 blast t24 | 24c | 3.89 | 6.24 | 1.61× | 0.1847 | *0.2082* | *1.13×* |
| 6 · 200 c12 | 24c | 2.98 | 5.41 | 1.82× | 0.1416 | *0.1806* | *1.28×* |
| 7 · 200 c8 | 24c | 2.63 | 5.07 | 1.93× | 0.1251 | *0.1693* | *1.35×* |
| 8 · 200 blast 3rep | 24c | 2.23 | 7.11 | *3.18×* | 0.1390 | 0.3074 | *2.21×* |
| pr2197 stock · 1k blast RR-only | 24c | — | 6.74 | — | — | 0.3544 | — |

In every patched loaded run the two ratios agree to two decimals (1.51–1.75×) — the
signature of equivalent work; in every pre-patch run they split. **One number: RocketRide
costs ~1.5–1.7× LangGraph's CPU per document under load, 3.4× sequentially.**

**The RocketRide effective-core ceiling on heterogeneous input:**

| envelope / RR threads | RR effective cores | LG on the same envelope | source |
|---|---|---|---|
| uncapped 32 / default or 32 (200-doc blast, pre-envelope) | 5.8–6.0 | 4.0 (150-doc blast, unpinned) | 14–15 Aug RR-only runs |
| 24 / 24 | 15.46 | 20.38 | Run 3 |
| 24 / 32 | 14.45 | 20.28 | Run 2 |
| 24 / 24 (10k) | 14.61 | 22.35 | Run 1 |
| 32 / 32 (1k, 10k) | 18.35, 18.14 | 25.99, 29.10 | Runs 12, 11 |
| 32 / 32, 64 (1k blast) | 17.48, 17.69 | — | 19 Aug probes |
| 32 / 32, independent harness (Govdocs 10k) | 16.93 | — | Shashi |
| 32, uniform corpus (24 arXiv papers × 417) | 29.7 | — | Shashi |

~15–18 effective cores regardless of offer — engine scheduling, not envelope. A uniform
corpus saturates; real-world size variance starves the dispatcher (head-of-line, now
confirmed from the latency side by Run 11's tail). The engine team's later explanation
(#2053, 26 Aug): admission width = connections × `threads`, one per-connection semaphore.

**Concurrency sweep (200 docs, Series 1 + Run 13 for shape):** seq p50 LG 0.98 s / RR
1.61 s → c8 47.6 / *47.7* chunks/s → c12 56.8 / *59.0* → blast t24 73.5 / *72.3* →
c128 (1k) 103.5 / 46.5. The 12 → 128 gap was never swept; the knee lies in it.

**Lines of pipeline code (M6):**

| component | LangGraph | RocketRide |
|---|---|---|
| pipeline definition | 96 | 78 (`.pipe`, semantic lines; 125 raw with editor chrome) |
| compute code | 107 | 0 |
| **pipeline subtotal** | **203** | **78** |
| serving / FastAPI | 459 (428 code in the 9 Sep recount) | 76 (≈33 are bug workarounds) |
| pipeline + serving | 662 (631 code) | 154 |
| client | 178 | 317 |
| total | 840 | 471 |

Pipeline-only RocketRide is 2.6× more compact, 4.3× with serving; a third of its serving
lines work around its own defects and its client is 1.8× larger. This measures authored
lines, not work done (the engine supplies Tika, the model registry and transport).

**Findings (numbered as in `BENCHMARK_RUNS.md` / `ENGINE_BUG_REPORT.md`):**

1. **Chunk duplication — found, diagnosed, patched.** `embedding_transformer` called
   `_flushDocuments()` then fell through to the default forwarding action, emitting
   `[A,B,C,A,B,C]` for 51/987 documents (repeat factor exactly 2, ~40% extra work).
   One-line patch (`return self.preventDefault()`); a permanent per-arm
   `self_duplication` gate. Diagnosis credit: Shashi's `BUG_CHUNK_DUPLICATION`. Fix is on
   the engine's `develop` branch (#2051).
2. **Character transposition in the parser — open.** ~14% of documents (1,409 of 9,913
   at 10k, the identical set in every run) come back with scrambled glyph order
   (`paymleimntit ation`), deterministic per document. Degrades retrieval on ~1 in 7
   documents. Both arms use Tika, so this is inside the engine's parse path.
3. **Failures reported as successes — open.** All four poison files, both modes:
   `action: "complete"` with an objectId and no error (§5.3).
4. **Concurrent connection ceiling ≈ 11.** Offered 128 or 200 connections the engine
   accepts 11; sockets multiplex to ~77 in flight; the funnel behind finding 6.
5. **Effective-core ceiling ~15–18** on heterogeneous input (table above).
6. **Head-of-line latency tail, unbounded in backlog** (Run 11: 2,050 s for 3 chunks).
   **Partly corrected 16 Sep (§5.8):** the same 3-chunk documents take 725–1,677 s
   with only 16 in flight, so the tail is mostly intrinsic parse time on those files,
   with queueing on top under load.
7. **Sequential CPU inefficiency 3.4×** (Run 13); the engine spins its pool regardless
   of load.
8. **Engine 3.3.1 cannot boot on Linux unpatched.** Pins `onnxruntime-gpu==1.20.1`
   (never published) in five `requirements*.txt` files of opt-in features; boot-time
   constraint compilation is global, so an unused NER feature prevents boot. Also
   affects 3.3.0; 3.2.1 booted. Workaround: sed the pin to 1.20.2 at image build
   (`aws_bench/findings/rr_linux_boot.md`).
9. **Text-free PDFs are a corpus property.** 87/10,000 (nested: 13 at 1k, 1–2 at 200)
   yield zero chunks on both arms; Tika reports `pdf:ocrPageCount: "0"`. Named by the
   census under `report` policy. Non-Tika probe still outstanding.
10. **Retracted:** "WebSocket rejected through Docker proxy" — our own missing
    `--host=0.0.0.0`. Also retracted (24 Aug): the `threads=256` "silent disable"
    reading — it was an explicit rejection (§5.5).

**Correctness gate status.** Chain (fail-closed): input integrity (SHA-256 vs manifest)
→ census (terminal record per offered doc; `CENSUS_EMPTY_POLICY` names text-free docs)
→ structure (384-dim, finite, unit-norm) → self-duplication → determinism (rep-to-rep or
concurrency-invariance with basis recorded) → provenance completeness. Cross-arm byte
parity is computed and reported, never gated. Run 11 PASS; Runs 12–13 would flip on
regeneration; Runs 1–8 FAIL on single-rep + old census policy; fault runs pass their own
gate.

**Remaining caveats (PDF):** single repetition everywhere but Run 8 (no error bars at the
final envelope); Series 2 client unpinned; native-saturation fault run never executed;
fault-gate treats unverified data isolation as pass and omits M5; non-Tika probe
outstanding; blast TTFR in RR records is upload-acknowledgement, not first result
(closed-loop TTFR is genuine); no committed unit tests for the metrics modules.

### 5.8 Batch-submission campaign: 10k docs in batches of 16 / 32 / 64, both arms (15–21 Sep)

Commissioned 15 Sep: the standard RocketRide engine, no tuning, 10,000 documents sent
in batches; the LangGraph arm was added 21 Sep at the same three batch sizes. Six cells,
all **RUN PASS**.

**What `b<B>` means, and why the arms are comparable without sharing an interface.**
New driver mode in both drivers (`15368a9`, `06aa315`). RocketRide submits **one
`send_files` call carrying B documents** on one connection, batch k+1 when batch k
returns; the engine schedules the B internally. LangGraph has no batch endpoint, so a
wave is **B concurrent HTTP requests released together and joined at a barrier**. The
wire protocols differ and must never be described as the same. What they share — and
what makes the cells comparable — is the offered load shape: at most B documents in
flight, and a wave boundary that waits for the slowest member. Per-document time runs
from wave release to that document's own completion on both arms, so both carry
intra-wave queueing and both are labelled batch-position latency.

**Held identical across all six cells.** Corpus `bench_corpus_n10000_off200`
(manifest sha `ec2797f0…`, the same 10,000 documents in the same order as Runs 1 and 11),
24 disjoint warm-up documents, 24-core arm envelope with the client isolated on cores
24–31, `OMP_NUM_THREADS=1` and the other three threading variables pinned to 1 on both
arms, Apache Tika 3.2.3 as the parser on both sides (embedded in the engine; a sidecar
for LangGraph, whose CPU is added to LangGraph's cost), one repetition per cell.

**RocketRide configuration was native and untuned:** `threads` unset (engine default
admission), one task, one connection, default single-task posture, PR #2197 patch off.
The engine binary carries the two standing corrections recorded in provenance and the
image labels — the Linux boot-pin fix, without which 3.3.1 cannot start on any Linux
host, and the embedding-transformer duplication correction, without which roughly 5% of
documents emit their chunk list twice. The tested system is "3.3.1 with the documented
duplication correction", never stock 3.3.1.

| metric | RR b16 | **LG b16** | RR b32 | **LG b32** | RR b64 | **LG b64** |
|---|---|---|---|---|---|---|
| ok / offered | 9,913 / 10,000 | 9,913 / 10,000 | 9,913 / 10,000 | 9,913 / 10,000 | 9,913 / 10,000 | 9,913 / 10,000 |
| chunks | 212,828 | 212,858 | 212,828 | 212,858 | 212,828 | 212,858 |
| span | 18,290 s (5.08 h) | **8,339 s (2.32 h)** | 15,103 s (4.20 h) | **6,502 s (1.81 h)** | 12,347 s (3.43 h) | **5,094 s (1.41 h)** |
| docs/s | 0.542 | 1.189 | 0.656 | 1.525 | 0.803 | **1.946** |
| chunks/s | 11.64 | 25.53 | 14.09 | 32.74 | 17.24 | **41.79** |
| effective cores of 24 | 3.79 | 2.85 | 4.63 | 4.05 | 5.64 | 5.63 |
| **CPU-s per doc** | 6.98 | **2.40** | 7.06 | **2.66** | 7.02 | **2.89** |
| CPU-s per chunk | 0.3253 | 0.1117 | 0.3286 | 0.1237 | 0.3271 | 0.1346 |
| latency p50 / p95 / p99 | 1.85 / 14.2 / 39.9 s | 1.22 / 8.4 / 22.4 s | 2.67 / 16.1 / 41.3 s | 1.96 / 9.4 / 23.3 s | 4.96 / 21.3 / 47.9 s | 3.58 / 12.3 / 26.1 s |
| **latency max** | **1,677 s** | **217 s** | **1,682 s** | **219 s** | **1,692 s** | **223 s** |
| wave span p50 / max | 15.7 / 1,677 s | 8.6 / 217 s | 28.2 / 1,682 s | 14.6 / 219 s | 44.0 / 1,692 s | 25.4 / 223 s |
| achieved concurrency mean | 2.49 | 3.12 | 3.63 | 5.17 | 6.69 | 9.93 |
| peak RSS · threads | 9.4 GB · 347 | 12.5 GB · 50 | 11.9 GB · 333 | 14.1 GB · 52 | 12.2 GB · 375 | 16.4 GB · 56 |
| **verdict** | **RUN PASS** | **RUN PASS** | **RUN PASS** | **RUN PASS** | **RUN PASS** | **RUN PASS** |

**Determinism, proven separately for each arm.** Within each campaign the three cells
were compared cyclically (b16 vs b32, b32 vs b64, b64 vs b16) with
`bench/cross_mode_determinism.py`. Every comparison, on both arms, read **9,913 / 9,913
documents byte-identical, zero mismatches**. Output on both frameworks is invariant to
batch size across a 4× range on 10,000 real documents, and each report records that as
its determinism basis — which is why every cell carries a full RUN PASS at one
repetition. (The tool prints "NOT INVARIANT" at the end of a single-arm run: that line
reflects the *absent* arm having no records to compare, not a mismatch. Read the per-arm
lines.)

**Two results, and they point in opposite directions.**

1. **For RocketRide, batch size buys throughput and nothing else.** From 16 to 64 the
   span falls 33% and chunks/s rise 48%, entirely because more cores are engaged
   (3.79 → 5.64); CPU per document is flat at 6.98 / 7.06 / 7.02, within 1.1% across the
   whole range. Even at 64 in flight it reaches 5.6 of 24 cores, consistent with the
   ~6-core single-task ceiling seen across the video track.
2. **For LangGraph, batch size buys throughput and costs efficiency.** Span falls 39%
   and chunks/s rise 64%, but CPU per document *rises* 20% (2.40 → 2.66 → 2.89) — it
   pays contention for the parallelism it actually uses. RocketRide's cost is flat
   precisely because it never converts the offered width.

   Across the three sizes LangGraph is **2.2× to 2.4× faster in wall time while spending
   2.4× to 2.9× less CPU per document**, and at b16 it does so holding *fewer* cores
   (2.85 vs 3.79). It also runs the corpus with about 50 threads against RocketRide's
   330–375.

**The latency tail separates the two arms by an order of magnitude, and names a defect.**
RocketRide's worst document takes ~1,680 s at every batch size; LangGraph's takes ~220 s.
With only 16 in flight there is no backlog to wait behind, so this is work, not queueing.
The documents responsible differ by arm: LangGraph's tail is its largest document
(`012852.pdf`, 1,959 chunks), which is what a fair queue looks like, while RocketRide's
tail is a set of **3-chunk** PDFs — `039660.pdf` 1,677 s, `011464.pdf` 725 s,
`008871.pdf` 417 s — that LangGraph parses in seconds using the same Tika 3.2.3 under the
same configuration. That isolates the cost to the engine's parse path on those files and
**corrects finding 6 in §5.7**: Run 11's 2,050 s tail is mostly intrinsic parse time,
with queueing on top, not queue position alone. Worth a product issue.

**Cross-arm output parity is unchanged from Runs 1 and 11.** Paired reports at each batch
size (`batch10k-paired-b<B>-…`) read **8,504 / 9,913 byte-identical, chunk-ratio median
1.0, zero hard violations** — the same 1,409-document set, the character-transposition
defect of §5.7 finding 2, reproduced identically at all three batch sizes. Those
directories are assembled from copies of the two source runs, which are never modified;
each carries a `PAIRING.md` stating what came from where, and a merged determinism file
holding both arms' own batch-size-invariance verdicts.

**Harness defects found and fixed during the campaign.** (a) `matched_run.sh` capped its
in-container cgroup sampler at 7,200 s, so every RocketRide cell — each ran 3.4 to 5.1
hours — lost CPU coverage for its tail and understated CPU per document by up to half. A
box-side supervisor running the same sampler uncapped covered the gaps;
`bench/merge_samplers.py` merges them (the runner's stream stays authoritative, the
original is kept as `sampler.runner.jsonl`, a genuine counter reset aborts), the M7 window
now end-anchors on the first sample after the window and publishes a `coverage` block, and
the sampler lifetime defaults to a day. (b) The merge tool's first monotonicity guard was
too strict: two samplers reading one cumulative counter microseconds apart interleave out
of order by ~0.05 CPU-s, which it misread as a container restart. All LangGraph cells ran
with the fixed sampler and have full coverage. Commits `db0487b`, `a1537bf`, `80b838d`,
`9520e9b`, `6e55aee`, `0490110`.

**S3.** RocketRide `leela/bench/batch10k-2026-09-15/batch10k-b<B>-20260916T065635Z/`;
LangGraph and the paired reports
`leela/bench/batch10k-lg-2026-09-21/batch10k-{lg-,paired-}b<B>-20260921T075403Z/`.

### 5.9 Node-level attribution: the bottleneck is the parse node (21–22 Sep)

§5.8 established that RocketRide spends ~2.9× LangGraph's CPU per document but could
not say where, because the engine publishes no per-stage timings. It does — it has all
along. The engine emits `apaevt_flow` events carrying `op: enter|leave` and a
`component` field, but only when the task starts with a `pipelineTraceLevel`, and no
event carries a timestamp. An earlier probe in this repo concluded they were unavailable
after passing `"1"` (a constant's integer value, not one of `metadata|summary|full`) and
then looking for the result in the `use()` return value rather than on the socket.
Switching it on correctly and supplying the clock client-side (commit `7f52a17`) turns
the black box transparent.

**Two corrections the raw traces force.** First, durations are **nested four deep** —
the engine drives the whole downstream chain synchronously inside `parse_1`'s lane call,
so the naive per-node totals are inclusive and indict the wrong node
(`preprocessor_1` appears to cost 490 s and actually costs 0.7). Everything below is
**exclusive self time**. Second, a document is not one call per node: `parse_1` receives
~11 lane calls per document, the others ~4.

**Tracing is free.** Same 200 documents, traced versus untraced, same driver:

| | control | traced | delta |
|---|---:|---:|---:|
| docs/s | 0.8083 | 0.8041 | −0.52% |
| chunks/s | 17.007 | 16.918 | −0.53% |
| CPU-s per doc | 5.471 | 5.510 | +0.71% |
| effective cores | 4.416 | 4.429 | +0.29% |

#### The bulk: same 200 documents, node by node, both arms

RocketRide's exclusive self time against LangGraph's `Server-Timing` stages on the
identical documents:

| stage | RocketRide | LangGraph | ratio | RR s/doc | LG s/doc |
|---|---:|---:|---:|---:|---:|
| **parse / extract** | **303.8 s** | **29.9 s** | **10.2×** | 1.519 | 0.149 |
| chunk | 0.7 s | 0.2 s | 3.9× | 0.003 | 0.001 |
| embed | 488.7 s | 486.6 s | **1.00×** | 2.443 | 2.433 |
| response / assemble | 0.9 s | ~0 | — | 0.005 | ~0 |

**Embedding agrees to 0.4%.** Same model, same throughput — the encoder was never the
problem, and neither is chunking. The entire node-level difference is parse, at 10×.

#### The tail: one document at a time, tracing on

Six documents run sequentially — no queueing to blame, and every flow event falls inside
exactly one document's window on the same clock, so attribution is arithmetic:

| doc | size | total | chunks | **parse** | preproc | embed | response | unattributed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 039660.pdf | 3.16 MB | 1564.7 s | 3 | **1564.4** | 0.00 | 0.3 | 0.00 | 0.1 |
| 011464.pdf | 1.25 MB | 678.2 s | 3 | **677.8** | 0.00 | 0.3 | 0.00 | 0.0 |
| 011730.pdf | 1.15 MB | 313.8 s | 7 | **313.1** | 0.00 | 0.7 | 0.00 | 0.0 |
| 012852.pdf *(text-heavy anchor)* | 1.83 MB | 217.7 s | 1959 | 3.2 | 0.22 | **210.0** | 0.30 | 4.0 |
| 001029.pdf *(size-matched control)* | 3.01 MB | 12.8 s | 97 | **2.4** | 0.00 | 10.1 | 0.01 | 0.3 |
| 001030.pdf *(floor)* | 0.05 MB | 0.6 s | 6 | 0.0 | 0.00 | 0.5 | 0.00 | 0.0 |

99.98% of the worst document's time is inside the parse node. The controls do the work
the controls exist to do: a **3.01 MB ordinary PDF parses in 2.4 s while a 3.16 MB
near-empty one takes 1,564 s** — 652× at the same file size — and the text-heavy anchor
behaves perfectly, embedding-dominated and within 7% of LangGraph end to end.

#### What is eliminated

| candidate | how it was ruled out |
|---|---|
| Tika version | both arms run Apache Tika **3.2.3**; the engine's jars were listed directly |
| Tika configuration | the engine's own `java/tika-config.xml` is materially identical to the sidecar config the LangGraph arm runs — same `PDFParser` params, both exclude `TesseractOCRParser`; the engine additionally excludes `GDALParser` |
| OCR | hard-disabled in the engine's Java bridge: `TikaApi.java:374` sets `OCR_STRATEGY.NO_OCR` |
| file size | 3.01 MB control parses in 2.4 s; 3.16 MB pathological file takes 1,564 s |
| queueing / head-of-line | sequential, one document in flight |
| chunking, embedding, response | measured at 0.003, 2.443 and 0.005 s/doc, embedding matching LangGraph to 0.4% |
| engine transport / scheduling | unattributed time ≤ 4 s on every document |

#### What remains, and where the source points

The cost is inside the engine's parse node, which is **not a Python node**: it is C++
driving an in-process JVM over JNI (`engLib/store/stack.cpp:610`). Three candidates are
visible in the 3.3.1 source, and one carries a comment conceding the trade
(`engLib/store/filters/parse/tika/callbacks.cpp:341-357`): the no-copy
`GetPrimitiveArrayCritical` read path is commented out in favour of
`GetByteArrayElements`, annotated *"Get the native copy of Java array"* and *"could be
less productive, but … safe"*, after a lock-contention regression **on remote sources**
— while the penalty applies to local files too. The other two are a mutex-guarded
callback queue drained by `pumpTikaCallbacks()` and a per-instance ping-pong thread
handing every object between the Tika thread and the pipe thread. Which of the three
dominates is not yet measured.

**Still open:** this is a *wall-time* decomposition. Per-node CPU (rather than elapsed)
needs the truncated-pipe ablation, which also explains why node self time summed to
794 s against ~1,089 CPU-s in the concurrent cell — roughly a quarter of CPU falls
outside any traced node call under concurrency, though under sequential execution
unattributed time is negligible.

**Records.** `s3://rocketride-benchmark-data/leela/bench/nodeprofile-2026-09-21/` —
`nodeprofile-{traced,control}-smoke20260922T022724Z/` (the bulk pair and its
perturbation control) and `pathology-20260922T024429Z/` (per-document attribution).
Each traced cell carries `flow_events.jsonl` and `node_timings.json` beside the usual
bundle. Harness: `7f52a17`, `521f44e`, `39f609a`.

---

## 6. Video track (aws_videobench)

Pipeline under test, both arms: one frame every 15 s (ffmpeg `fps=1/15`) → RF-DETR base,
threshold 0.3 → JSON per frame → `RecursiveCharacterTextSplitter` 4000/0 →
`multi-qa-MiniLM-L6-cos-v1` (384-d) → documents. RocketRide pipe
`benchmark_video_detect.pipe` (`webhook → frame_grabber → detect → preprocessor_langchain
→ embedding_transformer → response_documents`). Work parity is proven per run by gates:
input identity, frame parity (exact on AMI, VFR-band on films), detection ratio
(0.9–1.1), chunk ratio (0.8–1.25), workload ratio.

### 6.1 Bring-up and sizing (19–20 Aug) — no report files; from run records and notes

| run | what | result |
|---|---|---|
| `smoke-20260819T191632Z` | ES2002a × 2 reps through the **dual-lane** pipe (Whisper base transcript + detect), unpinned | webhook routes `.avi` straight to the video lane; PCM-in-AVI transcribes cleanly (2,275 words); 82 frame-arrays (84 expected); 384-dim/finite/norm-1; chunk order stable. **Whisper non-deterministic**: one 42-char segment differed across reps while all 24 detection chunks were byte-identical (temperature-fallback sampling, no config knob). Decision: detect-only pipe for the benchmark. Detection JSON is 87% of characters (83k vs 12k transcript). `use()` cold 105 s; 41 s / 65 s per 21-min meeting (~20× realtime whole-box). |
| `run30h-20260819T212432Z`, `…233913Z` | `ami30h` (60 × ~30 min, 31.43 h), dual-lane, blast | **failed 47/60 on ENOSPC** — the engine stores its own copy of every upload plus temp on the same volume; uploads accumulate for the pipeline-instance lifetime (~8 GB for 62 videos) and release only at terminate. Salvage: 13 clean docs, 0 duplicate chunks, ~4–5 effective cores, chunk mass varies ~5× by AMI room. Fix: delete raw cache after mux, disk preflight, waves with terminate between. |
| `rundetect-20260820T012644Z` | ami30h, **detect-only**, blast, engine default | clean 60/60, span 3,128 s = **36.2× realtime**, 5,453 chunks (15–205 per doc, p50 93), 0 duplicates, **5.85 average effective cores** (ramp 4.9 → 6.5) of 32. Warm 2 docs: 106 s detect-only vs 141 s dual-lane → ASR ≈ 25% of per-video cost. |
| `rundetect-20260820T062432Z` | same, `threads=32` | **no scaling**: 5.59 cores, span 2,783 s, output byte-identical 60/60. The same knob had taken PDF from 6 to 24 cores. |
| `capture-20260820T054250Z` | ES2016d, frames and embeddings captured | frames are never persisted by the pipe; 102 engine frame-arrays == 102 independent ffmpeg frames == duration/15; detections match visible content; embeddings reproduce to 1.06e-7. Engine `miniLM` = `multi-qa-MiniLM-L6-cos-v1`. |
| `s3test-20260820T184413Z` | corpus staged to S3, EBS copy deleted, waves pull S3 → `/dev/shm` (3.6 GB in 23 s ≈ 158 MB/s) | 30/30 ok, 36.8× (same as EBS), chunk hashes 30/30 identical to baseline. No S3/URL source exists in engine 3.3.1 (feature-request material). |
| `lgsmoke-20260821T000140Z` | LangGraph video arm, box | image builds on x86; ES2016d 102 frames / 20 chunks, ES2003b 140 / 48, IB4001 118 / 134 verified; ~20–28 s per 25–35-min video single-request (~65–75× RT, not a benchmark number); stage split decode ~55% / detect ~40% / embed ~5%. Cross-platform (box vs Mac) outputs differ ±1% chars, ±1 chunk. |

### 6.2 AMI, default posture, cross-arm (21–22 Aug) — Runs A, B, C

RocketRide: one `use()` (`use_existing=True`) → one task process, `threads` unset
(engine default 64 in-flight items), BLAS/OMP unset, blast. LangGraph: one uvicorn
process, one shared model, no inference lock, client cN. Unpinned, single rep — SIZING.

| metric | A: RR c6 | A: LG c6 | B: RR blast | B: LG c60 | **C: RR blast** | **C: LG c32** |
|---|---|---|---|---|---|---|
| run | `h2h-20260821T195300Z` | ″ | `native60-20260821T210828Z` | ″ | `native170-20260822T070136Z` | ″ |
| videos / footage h | 28 / 14.51 | ″ | 60 / 31.43 | ″ | **168 / 98.19** | ″ |
| census | 28/28 | 28/28 | 60/60 | 60/60 | 168/168 | 168/168 |
| span | 1,432.8 s | **376.8 s** | 3,108.8 s | **750.8 s** | 9,445 s (2.62 h) | **2,271 s (37.9 min)** |
| ×realtime | 36.46 | **138.63** | 36.40 | **150.73** | 37.43 | **155.65** |
| frames/s | 2.432 | 9.249 | 2.410 | 9.981 | 2.44 | 10.148 |
| chunks/s | 0.731 | 2.736 | 1.754 | 7.169 | 1.521 | 6.176 |
| chunks / video (work check) | 37.4 | 36.8 | 90.9 | 89.7 | 85.5 | 83.5 |
| frames / video | 124.5 | 124.5 (identical) | 124.9 | 124.9 (identical) | 137.2 | 137.2 (identical) |
| effective cores (of 32) | 5.45 | **19.47** | 5.87 | **25.64** | 5.98 | **26.84 (84%)** |
| threads activated | 998 | 225 | 3,804 | 1,960 | 4,049 | 2,932 |
| CPU-s / footage-min | 10.47 | **9.46** | **10.42** | 10.88 | **9.91** (RR's best) | 10.66 |
| CPU-s / frame | 2.615 | 2.363 | 2.622 | 2.738 | 2.532 | 2.725 |
| CPU-s / video | 325.4 | 294.1 | 327.4 | 341.9 | 347.4 | 373.8 |
| peak memory (cgroup incl. cache) | 23.7 GB | **6.2 GB** | 34.7 GB | **19.9 GB** | 42.9 GB | **28.8 GB** |
| cold-start to ready | 118 s | **59 s** | 104 s | **54 s** | 138 s | **72 s** |
| latency | p50 302 / p95 361 / p99 365 s (svc) | **p50 75 / p95 94 / p99 94 s** | batch: p50 2,110 / p90 3,324 / last 3,343 s | p50 359 / p95 437 / p99 447 s (svc) | batch: p50 5,565 / p90 9,430 / last 9,711 s | p50 430 / p95 708 / p99 787 s (svc) |
| time to first result | 160 s | **39 s** | 216 s (batch basis) | 172 s | 260 s (batch basis) | 18.4 s |
| LG stage split | — | frames 12% / detect 87% / embed 1% | — | 6 / 92 / 1 | — | 7 / 92 / 1 |
| LG framework overhead (total) | — | 0.14 s | — | 0.47 s | — | 1.35 s |
| $ per 1k footage-hours | $39.17 | **$10.30** | $39.23 | **$9.47** | $38.15 | **$9.17** |
| 30-min videos / day / box | 1,750 | 6,654 | 1,747 | 7,235 | 1,796 | **7,471** |
| workload ratio RR/LG | 1.020 | | 1.013 | | 1.024 | |
| verdict | FAIL by design (1 rep) + frame_law | | FAIL by design + frame_law; corpus_pin SKIP | | FAIL by design + frame_law calibration; **corpus_pin PASS (first)** | |

Gate history on these runs: `frame_law` in A/B caught a real corpus defect — ~10% of AMI
meetings have video/audio stream-length mismatch (ES2008c, ES2011c, IS1000a; manifest
durations came from audio) → fixed by ffprobe video durations at staging. In Run C those
are gone; what trips instead is the chunk-upper-bound clause (chunks ≤ 1.5×frames+1,
borrowed from the Haystack suite) on 4–5 ultra-dense Idiap rooms (IN1002 259 chunks /
165 frames, IN1007 294/160, IN1009, IS1003c), **identically on both arms** — a workload
property, needs a ~3× or chars-based bound. `chunk_parity_tight` warns on 3–5 EN videos
at ±2 chunks (different rfdetr builds).

Run C's one-sentence result: **the two arms spend the same CPU per unit of work (within
8%), but LangGraph schedules 27 of 32 cores while RocketRide's default posture holds
~6 — and that alone produces the 4.16× gap in wall time and cost.** RocketRide's default
ceiling was measured six times across corpus sizes, modes and media: 36.2–37.4× at
5.4–6.0 cores (the one 40.7× outlier on 20 Aug `threads=32` was scheduling variance;
CPU unchanged).

### 6.3 RocketRide thread sweep (24 Aug) — `rrsweep-20260824T061609Z`

16 AMI meetings in the 25–40-min band (9.66 h probed video duration; 10.26 h by source metadata) + 1 warm, blast, fresh engine per
configuration, single task (default posture).

| `use(threads=)` | effective cores | threads activated | ×realtime | frames/s | CPU-s / footage-min | CPU-s / frame | peak mem |
|---|---|---|---|---|---|---|---|
| unset (engine default 64) | 5.97 | 1,637 | 35.07 | 2.336 | 10.22 | 2.556 | 22.2 GB |
| 8 | 5.96 | 924 | 35.27 | 2.349 | 10.15 | 2.539 | 15.7 GB |
| 32 | 5.93 | 1,640 | 34.93 | 2.327 | 10.19 | 2.549 | 16.4 GB |
| 64 | 5.94 | 1,639 | 35.19 | 2.344 | 10.13 | 2.534 | 16.1 GB |
| 128 | aborted (user stop — satisfied) | | | | | | |

Dead flat. `threads=` provably resizes the task's pool (924 vs ~1,640 threads) and
changes nothing else. Read then as "architectural"; **corrected 24 Aug** from Ansh's
WS-1 memo and the engine source: the ~6-core ceiling is **per task** — one `use()` token
= one task process = one RF-DETR behind a real `threading.Lock`
(`nodes/detect/IGlobal.py:81`, `IInstance.py:106`) plus one asyncio loop; `threads=` is
a per-connection `asyncio.Semaphore` on in-flight items (`data_conn.py:117/138`), not
inference parallelism. Multi-task posture is the escape (§6.5).

### 6.4 Archive films, default posture (22–25 Aug)

Corpus `archive_films_v2`: 500 public-domain feature films frozen 23 Aug (JIT staging,
explicit-licence allowlist, 490 PD/CC0/BY + 10 NC-family classified), ~526 MB / ~89 min /
~361 frames per film, h.264 1080p-class derivatives of vintage prints. Frame parity is
approximate here (VFR prints; RR over-samples ~1.6%), never "identical".

| run | date | films / footage h | arm · mode | census | ×RT | frames/s | span | eff. cores | threads | CPU-s/fmin | CPU-s/frame | peak mem | $/1k fh | latency | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `archive10-20260822T190711Z` | 22 Aug | 8 / 10.89 | RR blast | 8/8 | **41.9** | 2.85 | 936 s | 5.70 | 1,132 | 11.03 | 2.704 | 25.5 GB | $34.08 | batch p50 1,192 s | RR's best single-task ×RT (long docs amortise); **frame_parity FAIL** — RR +6% frames on 5/8 (JungleBook 445 vs 420) |
| | | | LG c32 | 8/8 | 150.35 | 10.02 | 261 s | 17.63 | 927 | 9.74 | 2.435 | 14.9 GB | $9.50 | p50 213 s | 8 docs < c32 — under-offered; stage split decode 17% |
| `films10-20260823T170514Z` | 23 Aug | 8 / 11.49 | RR blast | 8/8 | 36.35 | 2.478 | 1,138 s | 5.95 | 1,129 | 13.0 | 3.18 | 26.0 GB | $39.28 | p50 1,427 s | CPU contaminated by pre-repair window (see 24 Aug) |
| | | | LG c32 | 8/8 | 129.48 | 8.634 | 320 s | 18.94 | 928 | 12.03 | 3.008 | 15.4 GB | $11.03 | p50 278 s | |
| `films50-20260823T175555Z` | 23 Aug | 48 / 71.58 | RR blast | 48/48 | 37.0 | 2.506 | 6,965 s | 6.25 | 3,694 | 10.64 | 2.618 | 37.8 GB | $38.59 | p50 4,745 s | |
| | | | LG c32 | 48/48 | 135.34 | 9.022 | 1,904 s | 24.0 | 2,178 | 11.8 | 2.951 | 34.0 GB | $10.55 | p50 1,120 / p99 1,869 s | 34 GB peak = the OOM curve cut short |
| `films500-20260824T034901Z` | 24 Aug | 498 | LG | — | — | — | — | — | — | — | — | — | — | — | **LangGraph OOM-killed at 97/498**: anon memory 1 → 42.4 GB over 5,000 s (all frames of a film decoded to RAM, ~2.3 GB per 90-min 1080p × 32 in flight; whole-file buffered uploads). No app error; 31 RemoteDisconnected + 370 refused. |
| `films10-20260824T030826Z` | 24 Aug | 8 / 11.49 | RR blast | 8/8 | 34.78 | 2.371 | 1,189 s | 6.69 | 825 | 11.54 | 2.822 | 25.8 GB | $41.06 | p50 1,489 s | regression after the pre-flight repair: TTL fix, epoch markers on both drivers, marker-windowed CPU (±15 s), probed video-duration authority, corpus_pin fail-closed, determinism 1-rep = SKIP/SIZING. Numbers from here on are not bit-comparable to earlier runs. |
| | | | LG c32 | 8/8 | 110.06 | 7.34 | 376 s | 22.55 | 905 | 12.29 | 3.072 | 15.3 GB | $12.97 | p50 324 s | |
| `films500-20260824T073256Z` | 24 Aug | 498 / 674.75 | **RR blast** | **498/498** | **35.01** | 2.367 | **69,385 s (19.27 h)** | 6.88 | 3,720 | 11.79 | 2.906 | 62.6 GB | **$40.79** | batch p50 38,058 / p90 64,691 / last 69,659 s; TTFR 228 s | 622 videos/day at the 81-min mean (1,680 30-min-equivalent); frame_law VFR warn, 155 exact |
| | | | LG c32 | partial | (375.9 on 97 films) | | | | | | | | | | the leg that died; report shows census FAIL and a meaningless workload ratio 5.95 — discard |
| `films10-20260825T060011Z` | 25 Aug | 8 / 11.49 | LG c32 (streaming fix, paired with the 24 Aug RR) | 8/8 | 135.32 | 9.024 | 306 s | 20.26 | 905 | 8.98 | 2.245 | 12.7 GB | $10.55 | p50 265 s | validation of commit `2d7533b` |
| `films500-20260825T061529Z` | 25 Aug | 498 / 674.75 | **LG c32** (streaming fix, paired with the 24 Aug RR) | **498/498** | **154.59** | 10.306 | **15,713 s (4.36 h)** | 25.46 (79.6%) | 2,162 | 9.88 | 2.47 | 51.7 GB (anon flat 9–14 GB) | **$9.24** | p50 941 / p95 1,377 / p99 1,975 s; TTFR 193 s | 2,738 videos/day (7,420 30-min-equivalent); stage split decode 5% / detect 94% / embed 1%; framework overhead 3.74 s over 675 h |

films500 paired verdict (`aws_videobench/runs/films500-sizing/report.txt`): validity
PASS, determinism NOT_RUN, evidence SIZING; input_identity 498, frame_parity VFR-band (46
exact), detection and chunk ratios in band, **workload ratio 1.026**. Gap 4.4× in
throughput and cost; LangGraph 16% cheaper per footage-minute at this scale (the per-unit
winner alternates within ~16% across runs). Settled: RocketRide's one-task ceiling is
35–37× on 6–7 cores regardless of corpus size (8 films and 498 films give the same
answer); the `.mp4` path, vintage codecs and 2.6× AMI's chunk mass all work.

### 6.5 Matched posture on AMI (25–26 Aug)

The question: is the ~6-core ceiling the engine's, or the default one-task posture's?
`MATCHED_POSTURE.md` defines `matched8x4_native`: RocketRide **8 tasks** (8 pipe copies
with distinct `project_id`, one `use()` each on its own client, no `use_existing`,
`ttl=0`, six BLAS/OMP vars = 4, `mem_limit 58g`, sharded blast `index % 8`, fail-closed
`task_census` reading each task's `/proc/<pid>/environ`) versus LangGraph **8
single-worker uvicorn processes** (ports 8201–8208, one model copy each, torch 4/1, one
`predict` at a time per worker, client c32 = 8 × 4, fail-closed `worker_census` via
`/meta`). Both fixtures warmed to every task/worker; a 30 s idle window measures the
"idle burden" of parked processes. All cells single rep, unpinned — SIZING.

| cell | run | posture | census | ×RT | **frames/s** | span | eff. cores (of 32) | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle burden | $/1k fh | latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| smoke attempt 1 | `matched8x4-ami-rep1-20260825T144717Z` | — | — | | | | | | | | | | | runner hung 2 h before the driver started (census watcher kept the `$()` capture open) — aborted, provenance only |
| smoke attempt 2 | `…20260825T170702Z` | — | — | | | | | | | | | | | driver exited at import (`ttl=0` rejected by the finite-ttl guard) — report crashed, no records |
| **smoke, N=8** (one video per process — null test) | `…20260825T181925Z` | RR `rr_matched_8x4` | 8/8 + task_census 8/8 | 160.1 | 10.68 | 144 s | 25.1 (windowed, after the loader fix) | 720 | — | 9.42 | 23.9 GB | **5.4 cores** | $8.92 | span bound by the longest video (EN2001a 88 min) |
| | | LG `lg_matched_8x4_c32` | 8/8 + worker_census 8/8 | 185.4 | 12.36 | 124 s | 23.9 (windowed) | 468 | — | 7.73 | 12.5 GB | ~0 | $7.70 | S3 report shows negative LG CPU — the `driver_cgroup.csv` interleave bug, fixed in `d09f974`; corrected figures from `MATCHED_POSTURE.md` §9 |
| **RR matched 8×4, full AMI** | `…20260826T064153Z` | `rr_matched_8x4`, admission unbounded (one batch per shard → engine admits 64/task, 114 ffmpeg decoders observed) | 168/168 + 8/8 | **166.08** | **11.069** | **2,082 s (34.7 min)** | 23.87 (74.6%) | 13,513 | 2.156 | 8.62 | 57.4 GB (at limit) | 3.9 | **$8.60** | batch TTFR 400 s; p50 1,992 / p90 2,355 / last 2,551 s |
| LG one-port 8 workers | `…20260826T075823Z` | `lg_workers8_1port_c32` — `uvicorn --workers 8` on one port, kernel picks the worker | 168/168 | 100.71 | 6.713 | 3,434 s (57.2 min) | 11.26 (35%) | 1,311 | **1.677** (lowest of any cell) | 6.71 | 24.9 GB | 1.1 | $14.18 | p50 233 / p95 1,582 / p99 2,174 s; **videos per worker pid 49/43/29/15/14/11/4/3** (even = 21); Ansh saw 48/29/28/25/19/7/7/5 |
| LG matched 8×4, 8 ports | — | `lg_matched_8x4_c32` | built and smoke-passed, **never run at N=168** (redirected) | | | | | | | | | | | |
| **RR 6 conn × `threads=32`** | `matched6x4-ami-rep1-20260826T155110Z` | `rr_matched_6x4`, first cell with the per-connection admission cap set explicitly (engine `threadCount: 32` in each task config; admission width 6×32 = 192 ≥ 168) | 168/168 + 6/6 | **177.23** | **11.813** | **1,951 s (32.5 min)** | 21.39 (66.8%) | 13,653 | **1.811** | 7.24 | 57.2 GB (at limit) | 4.33 | **$8.06** | TTFR 318 s; p50 1,877 / p90 2,182 / last 2,319 s |
| **equal-budget pair: RR** | `matched6x5-ami-rep1-20260826T172137Z` | `rr_matched_6x5` — 6 tasks × BLAS 5, `use(threads=4)` × 6 = 24 wide, 4 in flight per task | 168/168 + 6/6 | **191.02** | **12.732** | **1,810 s** | 24.79 | 2,302 | 1.947 | 7.79 | 44.9 GB | 5.16 | **$7.48** | TTFR 319 s; p50 1,180 / p90 1,962 / last 2,177 s |
| **equal-budget pair: LG** | ″ | `lg_inproc_6x5_c24` — ONE ordinary uvicorn, one shared model, torch 5/1, inference semaphore 6, client c24 | 168/168 | 185.0 | 12.331 | 1,869 s | 20.94 | 1,183 | **1.699** | 6.79 | **14.7 GB** | 0.7 | $7.72 | p50 264 / p95 473 / p99 505 s; TTFR 55 s |

Cross-arm on the 6×5 pair: all gates PASS, frame_parity exact 168/168, workload ratio
1.023. At the same math budget (6 concurrent inferences × 5 threads) and the same
in-flight width (24): **RocketRide +3.3% throughput with six model copies; LangGraph
13% less CPU per frame and one third of the memory with one copy.**

Findings from the matched campaign:

1. **Tokens are RocketRide's throughput lever.** Same engine, same corpus: 1 task →
   2.44 f/s at 6 cores; 8 tasks → 11.07 f/s at 24 cores (**4.5×**). The single-task
   `threads` sweep was flat because that argument bounds in-flight items. Reproduces
   Ansh's equal-config 8×4 cell (12.05 f/s, 30.0 cores) within 8%.
2. Out of the box, LangGraph is 4.2× faster and cheaper (default vs default) — a
   topology gap, not a framework gap.
3. With tokens, RocketRide edges LangGraph-default on throughput and is more
   CPU-efficient per frame (2.16 vs 2.73), at 2× the memory and a 3.9-core idle burden.
4. Admission is a real dimension: a whole-shard batch lets the engine run ~21 decoders
   per task, pinning memory at the limit (~8% cost vs a client-bounded feed).
   Fewer connections with an explicit cap (6 × 32) did identical work 6% faster on 2.5
   fewer cores; CPU per frame −16%. Both cells admitted the whole corpus (192 and 512
   slots ≥ 168), so neither is width-limited yet.
5. The one-port `--workers 8` deployment is what people would actually run, and the
   kernel's accept() skew leaves two thirds of the box idle behind a queue on the hot
   workers — a property of the serving stack, not of LangGraph's processing speed.

### 6.6 LangGraph posture sweep and best cells on AMI (31 Aug – 2 Sep)

Sweep on the first 96 AMI meetings (57.71 h) unless noted; every LG cell reads back pid
count, torch threads/interop and detect-concurrency from `/meta` (fail-closed). Postures:
`lg_inproc_KxT_cN` = one process with an inference semaphore of K and torch T;
`lg_matched_PxT_cN` = P single-worker processes, torch T, one inference each.

| run | posture | N | ×RT | frames/s | span | eff. cores | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle | $/1k fh | p50 / p95 / p99 (s) | stage split |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `lgsweep-lg_1proc_c32_t1-20260831T180210Z` | `lg_inproc_32x1_c32` | 32 | 186.05 | 12.397 | 425 s | 19.98 | 795 | 1.612 | 6.44 | 16.6 GB | 0.06 | $7.68 | 330 / 424 / 425 | 4 / 94 / 2 |
| `lgsweep-lg_1proc_c32_t1-20260831T221059Z` | `lg_inproc_32x1_c32` | 96 | 194.92 | 12.991 | 1,066 s | 23.21 | 1,101 | 1.786 | 7.14 | 17.6 GB | 0.22 | $7.33 | 301 / 554 / 568 | 3 / 93 / 4 |
| `lgsweep-lg_1proc_c32_t2-…` | `lg_inproc_32x2_c32` | 96 | 176.18 | 11.743 | 1,179 s | 22.86 | 1,160 | 1.947 | 7.79 | 16.2 GB | 0.44 | $8.11 | 350 / 634 / 653 | torch 2 loses |
| `lgsweep-lg_2proc_c32_t1-…` | `lg_matched_2x1_c32` | 96 | 204.34 | 13.62 | 1,017 s | 24.43 | 1,030 | 1.794 | 7.17 | 17.3 GB | 0.05 | $6.99 | 270 / 516 / 527 | **worker_census FAIL** (readback mismatch) — diagnostic |
| `lgsweep-lg_4proc_c32_t1-…` | `lg_matched_4x1_c32` | 96 | 205.81 | 13.717 | 1,010 s | 24.16 | 1,132 | 1.761 | 7.04 | 18.5 GB | 0.21 | $6.94 | 255 / 498 / 505 | 4 / 91 / 5 |
| `lgsweep-lg_4proc_c48_t1-…` | `lg_matched_4x1_c48` | 96 | **210.40** | **14.024** | 987 s | 26.32 | 2,054 | 1.877 | 7.51 | 25.1 GB | 0.67 | $6.79 | 362 / 697 / 719 | 4 / 92 / 4 |
| `lgsweep-lg_8proc_c32_t1-…` | `lg_matched_8x1_c32` | 96 | 206.73 | 13.779 | 1,005 s | 24.03 | 1,074 | **1.744** | 6.98 | 21.9 GB | 0.88 | $6.91 | 248 / 477 / 510 | 4 / 91 / 5 |
| **`lgbest-lg_4proc_c48_t1-20260902T174032Z`** | **`lg_matched_4x1_c48`** | **168** | **220.73** | **14.712** | **1,567 s** | 28.68 | 2,139 | 1.949 | 7.80 | 30.3 GB | 0.62 | **$6.47** | 386 / 688 / 725 | 4 / 90 / 5 |
| `lgbest-lg_4proc_c64_t1-20260902T195854Z` | `lg_matched_4x1_c64` | 168 | 216.87 | 14.455 | 1,595 s | 27.67 | 2,634 | 1.914 | 7.65 | 34.6 GB | 0.76 | $6.58 | 491 / 903 / 987 | 4 / 92 / 4 |
| `lgbest-lg_8proc_c32_t1-20260902T174032Z` | `lg_matched_8x1_c32` | 168 | 216.22 | 14.412 | 1,599 s | 26.62 | 1,130 | **1.847** | 7.39 | **25.3 GB** | 0.23 | $6.60 | **260 / 465 / 481** | 5 / 88 / 7 |

Settled for LangGraph: torch 1 thread per inference is right on any corpus (torch 2
costs 10–23% while holding *more* cores); process count saturates at 4 (8 vs 4 within
noise); c48 over c32 buys ~2% on AMI at the price of memory and latency; the single
ordinary process with a 32-deep semaphore is within ~7% of the best multi-process cell.

### 6.7 RocketRide best cells on AMI (2 Sep) and the AMI best-vs-best

In-flight width held at 32 box-wide (`tasks × inflight`); the variable is the tasks /
BLAS-threads split.

| run | posture | ×RT | **frames/s** | span | eff. cores | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle burden | $/1k fh | batch TTFR; p50 / p90 / last |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `rrbest-16x2-ami-20260902T131225Z` | `rr_matched_16x2` — 16 tasks × OMP 2, 2 in flight per task | 229.76 | 15.314 | 1,505 s | **29.62** | 2,819 | 1.934 | 7.74 | 45.7 GB | 5.69 | $6.22 | 523 s; 1,335 / 1,988 / 2,149 |
| **`rrbest-32x1-ami-20260902T131225Z`** | **`rr_matched_32x1`** — 32 tasks × OMP 1, 2 in flight per task | **243.24** | **16.213** | **1,422 s (23.7 min)** | 28.74 | 5,520 | **1.773** | **7.09** | 61.1 GB | 8.91 | **$5.87** | 903 s; 1,672 / 2,353 / 2,515 |

**AMI 168 best-vs-best (SIZING):**

| | RocketRide `rr_matched_32x1` | LangGraph `lg_matched_4x1_c48` | edge |
|---|---|---|---|
| frames/s · ×RT | **16.213 · 243.2** | 14.712 · 220.7 | **RR +10.2%** |
| effective cores | 28.74 | 28.68 | tie |
| CPU-s / frame | **1.773** | 1.949 | RR −9% |
| peak memory | 61.1 GB | **30.3 GB** | LG half |
| idle burden | 8.9 cores | **0.6** | LG |
| $ / 1k footage-hours | **$5.87** | $6.47 | RR |
| vs each arm's default | RR 6.6× its own default (2.44 f/s) | LG 1.45× its own default (10.15) | |

For contrast, Ansh's WS-1 cells on the same corpus: RR default 2.44 f/s / 6.03 cores;
RR parity 16×2 12.75 f/s / 29.4 cores (92%); RR equal-config 8×4 12.05 f/s / 30.0 cores;
LangGraph-in-process 8×4 9.27 f/s. Our default number reproduced his within 0.2%.

### 6.8 Films with both arms tuned (3–5 Sep)

**4a. AMI optima applied to films50** — `filmsbest-50-20260903T040810Z`, 48 measured +
2 warm (71.63 h):

| arm · posture | census | ×RT | frames/s | span | eff. cores | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle | $/1k fh | latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RR `rr_matched_32x1` (1 in flight per task) | 48/48 + 32/32 | 131.79 | 8.933 | 1,957 s | 23.51 | 2,789 | 2.632 | 10.71 | 58.9 GB (at limit) | 8.55 | $10.84 | TTFR 1,099 s; p50 2,053 / p90 3,535 / last 4,047 |
| **LG `lg_matched_4x1_c48`** | 48/48 + 4/4 | **151.58** | **10.105** | **1,701 s** | 21.52 | 2,747 | **2.129** | 8.52 | 46.3 GB | **0.27** | **$9.42** | p50 1,202 / p95 1,554 / p99 1,632 s |

Cross-arm: detection ratio PASS on all 48, chunk ratio PASS, workload ratio 1.019.
**LangGraph 13% ahead** — the reverse of AMI. `filmsbest-500-20260903T040810Z` (498
films at these configs) was stopped by request at ~40 films; no usable numbers.

**4b. RocketRide posture sweep on films50** — `rrfilms-*-20260903T202644Z`, RR only,
in-flight 32 box-wide in every cell:

| posture | ×RT | frames/s | span | eff. cores | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle | $/1k fh |
|---|---|---|---|---|---|---|---|---|---|---|
| 32 × OMP 1 (AMI optimum, from 4a) | 131.79 | 8.933 | 1,957 s | 23.51 | 2,789 | 2.632 | 10.71 | 58.9 GB | 8.55 | $10.84 |
| **16 × OMP 2** | **153.63** | **10.411** | **1,679 s** | 25.40 | 3,070 | **2.44** | 9.92 | 47.8 GB | 5.22 | **$9.30** |
| 8 × OMP 4 | 135.80 | 9.204 | 1,899 s | 27.80 | 2,909 | 3.02 | 12.28 | 41.6 GB | 3.90 | $10.52 |

**The optimal posture is corpus-dependent and non-monotonic**: 16×2 beats 32×1 by 16.5%,
and 8×4 is worse again despite holding *more* cores (intra-op scaling is sub-linear).
The peak tracks the decode share: AMI (4% decode) wants 32 lanes, films (~15%) want 16.
Shashi's harness independently ordered them the same way (16×2 = 12.52 f/s vs 32×1 =
10.26, +22%). Caveat: probably cache-cold — 32×1 ran first and evicts the corpus; Shashi
measured 18% between byte-identical reps from page-cache state alone (11% iowait, gp3 at
its 125 MB/s ceiling). A prewarm was added afterwards.

**5. films100, both arms tuned, cache-prewarmed** — `films100-*-20260905T023331Z`, 98
measured + 2 warm (145.08 h). 98 films gives LangGraph ~2 waves at c48 (on films50 it got
one wave and converted only 21.5 cores) and RR 16×2 ~6 waves.

| cell | config | census | ×RT | **frames/s** | span | eff. cores | threads | CPU-s/frame | CPU-s/fmin | peak mem | idle | $/1k fh | latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **`lg8x48t1`** | LG 8 proc × c48 × torch 1 | 98/98 + 8/8 | **198.80** | **13.253** | **2,627 s** | 25.94 | 2,745 | 1.958 | 7.83 | 53.6 GB | **0.1** | **$7.18** | p50 965 / p95 1,870 / p99 2,301 s |
| `lg4x48t1` | LG 4 proc × c48 × torch 1 | 98/98 + 4/4 | 195.73 | 13.049 | 2,668 s | 24.92 | 2,392 | **1.910** | 7.64 | **39.4 GB** | 0.3 | $7.30 | p50 986 / p95 1,975 / p99 2,417 s |
| `rr16x2` | RR 16 tasks × OMP 2, 2 in flight | 98/98 + 16/16 | 163.89 | 11.103 | 3,187 s | 26.95 | 2,925 | 2.427 | 9.87 | 56.8 GB | 5.38 | $8.71 | TTFR 893 s; p50 2,611 / p90 3,946 / last 4,375 |
| `lg4x48t2` | LG 4 proc × c48 × **torch 2** | 98/98 + 4/4 | 151.45 | 10.097 | 3,448 s | 28.59 | 2,882 | 2.831 | 11.32 | 53.7 GB | 0.9 | $9.43 | p50 1,385 / p95 2,567 / p99 3,321 s |

All four PASS (frame_law VFR warn on RR). Workload ratio RR/LG 1.021. Settled:
**LangGraph wins on films by 19.4%** and on efficiency (1.958 vs 2.427 CPU-s/frame) with
0.1 idle cores against 5.38; the wave hypothesis was right (21.5 → 24.9–25.9 cores);
torch 2 costs LangGraph 23%; 8 vs 4 processes is +1.6%, inside noise. Gap: RocketRide
has only one posture on films100 (its 16×2 optimum came from a cache-cold films50 where
32×1 was crippled by 1.5 waves) — RR is the under-tuned arm on the corpus we would quote.

**Where films and AMI disagree, and why:**

| | AMI 168 (4% decode) | films100 98 (~15% decode) |
|---|---|---|
| RocketRide best | 32×1 = **16.213 f/s** | 16×2 = 11.103 |
| LangGraph best | 4×c48 = 14.712 | 8×c48 = **13.253 f/s** |
| winner | RR **+10.2%** | LG **+19.4%** |
| RR cores → CPU-s/frame, AMI → films | 28.74 → 26.95 (−6%) | 1.773 → 2.427 (**+37%**) |
| LG cores → CPU-s/frame, AMI → films | 28.68 → 25.94 (−10%) | 1.949 → 1.958 (**+0.5%**) |
| decode cost per frame (LG measured / RR inferred) | 0.078 / ~0.02 | 0.294 / **~0.82** |

LangGraph's cost per frame is flat between corpora; RocketRide's rises 37%, and since
detection is the same work on both arms the difference is in frame extraction. Leading
hypothesis from engine source: `ai/common/avi/reader.py` cannot stream mp4/avi from
stdin, so the engine **spools every file to a temp cache and re-reads it** — harmless at
AMI's 74 MB, 2.7× more spool I/O per frame at films' 526 MB, on a gp3 volume at its
125 MB/s ceiling with 16 tasks doing it at once; OMP spin-wait turns I/O stalls into
CPU-seconds. Unproven; the decisive test (same cell from `/dev/shm`) has not been run.

### 6.9 PR #2197 video A/B (7 Sep) — `pr2197-{stock,patched}-20260907T024121Z`

AMI 168, RR default posture (1 task, `threads` unset), blast, 1 rep each, all gates PASS:

| | stock | patched | delta |
|---|---|---|---|
| ×RT · frames/s | 36.72 · 2.448 | 36.75 · 2.450 | **+0.08%** |
| span | 9,416 s | 9,409 s | |
| effective cores | 6.13 | 6.12 | |
| CPU-s / frame · / footage-min | 2.504 · 10.01 | 2.499 · 10.00 | −0.17% per chunk |
| peak memory | 43.5 GB | 37.0 GB | |
| $/1k fh | $38.89 | $38.86 | |

Structurally inert: every 4,000-char detection-JSON chunk exceeds MiniLM's 512-token
limit, so every chunk truncates to exactly 512 tokens and there is no padding to remove.
(The PDF pipeline, whose chunks vary, is where the patch showed a signal — §5.6.)

### 6.10 Cross-cutting video results and findings

**RocketRide's default single-task ceiling, every measurement** (unpinned, one `use()`):

| run | corpus | ×RT | effective cores |
|---|---|---|---|
| rundetect 20 Aug (default / `threads=32`) | 60 AMI | 36.2 / 40.7 (outlier) | 5.85 / 5.59 |
| s3test 20 Aug | 30 AMI | 36.8 | — |
| h2h 21 Aug (c6) | 28 AMI | 36.46 | 5.45 |
| native60 21 Aug | 60 AMI | 36.40 | 5.87 |
| native170 22 Aug | 168 AMI | 37.43 | 5.98 |
| archive10 22 Aug | 8 films | 41.9 | 5.70 |
| films10 23 / 24 Aug | 8 films | 36.35 / 34.78 | 5.95 / 6.69 |
| films50 23 Aug | 48 films | 37.0 | 6.25 |
| films500 24 Aug | 498 films | 35.01 | 6.88 |
| thread sweep 24 Aug (unset/8/32/64) | 16 AMI | 34.9–35.3 | 5.93–5.97 |
| pr2197 stock / patched 7 Sep | 168 AMI | 36.72 / 36.75 | 6.13 / 6.12 |

**The engine's real ceiling is per task, not per engine.** Multi-task postures reach
24–30 effective cores and 11–16 frames/s on AMI. Mechanism (verified in the 3.3.1 image
and the nodes package, 25–26 Aug, after one wrong intermediate reading): per task process,
RF-DETR inference is serialised by a real `threading.Lock`
(`nodes/detect/IGlobal.py:81`, taken in `IInstance.py:106`); decode and emit run outside
it; box-wide inference concurrency = number of task processes; `threads=` is the
per-connection admission semaphore (`data_conn.py`), adding decoders and memory, not
forward passes. The `--modelserver` flag would replace the lock with `nullcontext`, but
the model server is not in the OSS repo.

**No shipped knob or document reaches the multi-task posture.** Engine argv is only
`--host/--port/--modelserver/--base_port/--verbose/--saas`; no config file, no
concurrency env var; the TypeScript API doc says `threads` defaults to 1 (real default
64); the VS Code extension holds one client and calls `use()` without threads or
`useExisting`. Filed as **#2128** (26 Aug). The batch-scheduler starvation seen on PDF
is **#2053** (19 Aug; engine team replied 26 Aug with the admission model, our rerun
matrix committed publicly). The `threads=256` "silent disable" claim in **#2052** is
retracted.

**Workload character** (AMI, per arm identical): 23,049 frames, ~197k detections
(RR 197,062 vs LG 196,001), ~14k chunks. Chunk mass varies **8.5× by room type** —
ES 29.6 chunks/video (57.9 per footage-hour), EN 35.6, TS 88.7, IS 133.2, IB 164.9, IN
252.7 (309.8/h). Densest: IN1016 (319 chunks), IN1001, IN1007. Films: ~361 frames and
~105–108 chunks per film, 2.6× AMI's chunk mass. Footage denominator: ffprobe
video-stream seconds (98.19 h source metadata vs 96.06 h probed for `ami_full`, a 2.2%
difference affecting only "per footage" rows of Run C; frames identical across cells).

**Gates that caught real things:** `frame_law` (AMI A/V stream-length mismatch, twice;
then the dense-room calibration item), `frame_parity` (RR's reader over-samples VFR
prints — 445 vs 420 frames on JungleBook), memory inflation (cache vs anon: the films500
OOM), `worker_census` (a mis-configured 2-process LG cell), the thread-vs-cores gap, and
the kernel accept() skew on one-port deployments. `corpus_pin` has been fail-closed since
Run C.

**Cross-team corroboration.** Shashi's independent RocketRide-vs-Haystack video run
(`vfull170-0823`, 170 AMI meetings, 3 reps, envelope `RR_THREADS=32`, `OMP_NUM_THREADS=1`,
his own detect pipe): RR 22.7× at **2.49 cores flat, cv 0.6%**; Haystack 195.5× (cv
6.9% at 170 videos, having been 55% at 50 — a small-N artefact, retracted); frames and
detections exactly equal cross-arm; sequential RR wins (21.8× vs 20.0×, 1.4 vs 28 GB).
His core-ceiling constant differs from ours by pipe (2.5 vs 5.5–6.0) but the
flat-vs-threads shape is the shared, twice-confirmed finding. He also established that
AMI cameras cap at ~60 min on video while audio runs to 90 min (97.5 video-hours vs 100.2
audio-hours), which is why every manifest here uses ffprobe video durations.

**Video caveats.** Every video cell is single-rep on an unpinned box — SIZING. Latency
is never compared across arms in different modes (batch completion vs HTTP request).
Page-cache state can move a films cell ±15% (prewarm added 5 Sep; earlier cells are
possibly cold). Detection parity on films is ~1–3%, not exact (swscale colour-space
hypothesis, unverified). The publishable campaign (cpuset envelope with driver, samplers
and keepalive outside it; ≥ 3 reps with alternating arm order; medians with spread;
determinism exercised) has not been run on either corpus.

### 6.11 Node-level attribution on AMI, both arms: the bottleneck is the detect node, and it is a queue (22 Sep)

The PDF method of §5.9 applied to video, both arms, same videos, default posture on
both (RocketRide one task / `threads` unset / its native batch; LangGraph one process /
c32), unpinned as every AMI run has been. RocketRide's video driver gained the same
`RR_TRACE` mechanism (`abbd224`); LangGraph's per-node timings were already recorded on
every video and had never been compared node for node. Built by two Opus subagents
under Fable orchestration, verified against a fake SDK including a proof that the
driver is byte-identical with tracing off. Four cells: RocketRide traced on 24
meetings (17.9 h), LangGraph on the same 24, then six room-diverse videos one at a time
on each arm — two dense Idiap rooms (IN1007, IN1016), two sparse ES rooms (ES2002a,
ES2016d), the 88-minute EN2001a, and TS3009c. The video control cell was skipped at
Leela's request; the PDF calibration (tracing costs 0.5%) stands, and the traced cell's
39.9× realtime sits inside the 35–41× band of every prior default-posture run.

**The same nesting trap, one level deeper.** The video chain nests five deep —
`frame_grabber_1 ⊃ detect_1 ⊃ preprocessor_1 ⊃ embedding_1 ⊃ response_1` — so the raw
inclusive numbers hand the whole pipeline to the frame grabber. Everything below is
exclusive self time from `bench/flow_selftime.py`.

#### Six videos, one at a time, both arms — the clean comparison

| stage | RocketRide | LangGraph | ratio | RR s/video | LG s/video |
|---|---:|---:|---:|---:|---:|
| frame_grabber / frames | 2.5 s | 100.6 s | 0.02× | 0.42 | 16.77 |
| **detect** | **371.8 s** | **109.8 s** | **3.39×** | **61.96** | **18.30** |
| preprocessor / chunk | 0.1 s | 0.1 s | — | 0.02 | 0.01 |
| embedding | 15.8 s | 13.0 s | 1.22× | 2.64 | 2.17 |
| **total** | 390.2 s | 223.5 s | 1.75× | 65.0 | 37.3 |

RocketRide's node share: **detect 95.2%**, embedding 4.1%, frame grabber 0.6%.
LangGraph's: detect 49%, frames 45%, embed 6%. Per video, both arms:

| video | chunks | RR detect | RR embed | RR grabber | LG frames | LG detect | LG embed |
|---|---:|---:|---:|---:|---:|---:|---:|
| IN1016 (dense) | 319 | 89.3 | 6.00 | 0.45 | 23.6 | 26.4 | 4.89 |
| EN2001a (88 min) | 52 | 89.3 | 0.96 | 0.46 | 24.3 | 27.0 | 0.83 |
| TS3009c | 138 | 64.2 | 2.53 | 0.43 | 17.7 | 18.7 | 2.25 |
| IN1007 (dense) | 294 | 60.0 | 5.48 | 0.44 | 16.5 | 17.5 | 4.24 |
| ES2016d (sparse) | 20 | 38.3 | 0.38 | 0.35 | 10.4 | 11.1 | 0.38 |
| ES2002a (sparse) | 24 | 30.7 | 0.47 | 0.37 | 8.2 | 9.1 | 0.43 |

Embedding tracks chunk count on both arms and agrees within 22% — as on PDF, the encoder
is not in question. Chunking is free on both.

#### What the CPU says the wall time means

| cell | span | frames/s | ×RT | cores | **CPU-s / frame** |
|---|---:|---:|---:|---:|---:|
| RocketRide, 24 at once | 1,615 s | 2.66 | 39.9 | 5.43 | **2.04** |
| LangGraph, 24 at once | 389 s | 11.03 | 165.4 | 23.81 | **2.16** |
| RocketRide, one at a time | 413 s | 2.41 | 36.1 | 5.26 | **2.19** |
| LangGraph, one at a time | 228 s | 4.37 | 65.5 | 8.77 | **2.01** |

**CPU per frame is the same on both arms, in both modes, within 9%.** The per-frame
work is identical. RocketRide's detect takes 3.4× LangGraph's wall time per video
because it runs on 5.3 cores while LangGraph's runs on 8.8 with one video in flight —
and RocketRide's core count does not move when 24 videos are offered (5.26 → 5.43).

#### The bottleneck, measured at the node

| detect_1 wall time per frame | one video in flight | 24 videos in flight | inflation | throughput gained |
|---|---:|---:|---:|---:|
| RocketRide | 0.374 s | **7.38 s** | **19.7×** | +10% (36.1 → 39.9×) |
| LangGraph | 0.110 s | 1.46 s | 13.3× | +152% (65.5 → 165.4×) |

Under load RocketRide's detect node spends ~7 s per frame **waiting** and ~0.37 s
computing: with 24 videos in flight, ~20 detect calls sit open at any instant behind
the one that holds the per-task device lock (`nodes/detect/IGlobal.py:81`,
`IInstance.py:106`). LangGraph's detect also inflates under load, but that is CPU
contention while saturating 24 cores, and it buys 2.5× throughput; RocketRide's is lock
contention that leaves 26 cores idle and buys 10%. The 4.15× span gap between the two
24-video cells is the same 4.16× as Run C, now with a node and a mechanism attached.

**So on video the node is `detect_1`, and the bottleneck is serialization at that node,
not the work inside it — the opposite shape from PDF, where the node (`parse_1`) does
10× more work per document.** The `threads=` knob cannot help (it bounds in-flight
items, not inferences), which is why the 24 Aug thread sweep was flat; the multi-task
posture can, which is why 8 tasks reached 24 cores in §6.5.

**One thing not settled.** RocketRide's frame grabber shows 0.42 s of self time per
video against LangGraph's 16.8 s of single-threaded ffmpeg — 2.5 ms per output frame,
which cannot be a full decode of 15 s of DivX. Yet CPU per frame matches LangGraph, so
the decode cost is real and lands somewhere other than the grabber's own window: most
likely lazily inside the detect call, or on the engine's reader thread outside any
traced lane call. It is not a bottleneck on AMI either way (the totals say so), but on
the films corpus — where §6.8 inferred a 2.8× decode penalty by subtraction — the same
traced run would settle whether that penalty is decode or something else.

**Records.** `s3://rocketride-benchmark-data/leela/videobench/nodeprofile-ami-2026-09-22/`
— `nodeprofile-ami-{rr-traced,lg,rr-seq,lg-seq}-20260922T214831Z/` and
`nodeprofile-ami-analysis-20260922T214831Z/` (`selftime_*.txt/json`, paired report).
Harness: `08e30df` (checkpoint of prior uncommitted video work), `abbd224`.

---

## 7. Cross-track synthesis

**What is proven (publishable grade).** One run: PDF Run 11, 10,000 documents at C=128
on 32 cores — LangGraph 121.0 vs RocketRide 49.9 chunks/s; 29.1 vs 18.1 effective cores;
p50 service latency 19.3 vs 28.4 s; TTFR 2.1 vs 1.0 s; CPU per document 5.17 vs 7.81;
both arms M0 PASS with determinism by concurrency-invariance; cross-arm divergence
(1,409 transposed documents) reported as a product finding. Supporting proofs:
rep-to-rep byte identity (Run 8, both arms ×3), zero blast radius and byte-level data
isolation under fault injection (both arms, both modes), and 87/10,000 text-free
documents as a corpus property.

**What is established as sizing evidence (consistent across many single-rep runs).**

| claim | PDF | video |
|---|---|---|
| RR default posture leaves most of the box idle | 15–18 of 24–32 cores | ~6 of 32 cores (11 measurements, 34.8–41.9×) |
| the cause is scheduling / topology, not per-unit cost | CPU/doc ratio 1.5–1.7× under load | CPU per footage-min within ~10% of LG at default; RR *more* efficient per frame when multi-task |
| a configuration exists that closes the gap | 32-thread blast = RR's best 53.7 chunks/s, still 2.3× behind LG | 32 tasks × 1 thread = 16.2 f/s, **+10% over LG's best on AMI**; 16×2 on films still 19% behind LG's best |
| the configuration is undocumented and not the default | `threads` semantics (1–64, admission = connections × threads) came from the engine team on an issue | tokens/tasks lever undocumented; VS Code extension cannot set it (#2128) |
| sequential / uncontended cost | RR 3.4× LG's CPU per doc; RR p50 1.6 s vs 1.0 s | RR wins per-stream on Shashi's harness (memory 20× lighter) |
| memory | RR lower peak RSS at scale (14.9 vs 17.7 GB at 10k); RR recovers 16–31 MB after faults vs LG 646–1,734 MB | RR multi-task needs 45–61 GB (one model copy per task) vs LG 25–30 GB |
| latency tail | RR head-of-line: 2,050 s for 3 chunks; no per-document SLA possible under load | batch API: no per-video latency at all; LG p50 260–430 s at c32–c48 |
| where the optimum lies | 24-core envelope is the efficiency point; extra cores buy 3–6% | corpus-dependent and non-monotonic (AMI wants 32 lanes, films 16) |

**Engine findings filed or reported upstream.** Linux boot pin (RR-1), chunk duplication
(RR-2, fixed on `develop` as #2051), failures reported as successes (RR-3), batch scheduler
idle 30–50% (RR-4 / #2053), throughput configuration undocumented (#2128), PR #2197 A/B
results (video inert, PDF +0.6% / −1.7% CPU per chunk, microbench +4.2% embed stage; reply
to Stepan pending), parser character transposition (open, unfiled as an issue), Whisper
non-determinism, no S3/URL source, upload retention on the engine's volume, VFR
over-sampling in the video reader.

**Open items, in order of value.**

1. Three repetitions per cell for the PR #2197 PDF A/B at N=1,000 (~40 min box time) —
   turns +0.61% into a quotable number; fix the two `pr2197_pdf_ab.sh` bugs first; then
   reply to Stepan. Commit the uncommitted A/B harness.
2. RocketRide 32×1 on films100 (~70 min) — the missing cell before quoting the films
   headline; then the `/dev/shm` decode test to settle the spool hypothesis.
3. The #2053 rerun matrix promised publicly: {mixed 10k GovDocs1, uniform 24-arXiv × 417}
   × {single connection batch / per-doc / multi-connection} × threads {32, 4}.
4. The publishable video campaign: cpuset envelope, ≥ 3 reps alternating arm order, seq
   baselines, on AMI 168 at each arm's best posture; the `frame_law` bound
   recalibration first.
5. PDF: 3-rep at the 32-core envelope; the native-saturation fault run; the non-Tika probe
   for the text-free allowlist; fault-gate M5 wiring.
6. LangGraph matched 8×4 on 8 ports at N=168 (built, never run) and the RR bounded-admission
   8×4 cell, if the matched pair is to be quoted as such.

---

## Appendix A — S3 run index

Bucket `s3://rocketride-benchmark-data/leela/`. Each PDF bundle holds `environment.txt`,
`provenance.json`, `image_ids.txt`, `engine_sha.txt`, `report.txt` / `RUN_REPORT.json`
and per arm/rep `per_doc.jsonl` + `sampler.jsonl` + `manifest.json`. Each video bundle
holds `report.txt`, `provenance/` (git state, host, images, `run_config.txt`, pip freeze,
model hashes), and per arm `per_doc.jsonl`, `manifest.json`, `engine_cgroup.csv`,
`driver_cgroup.csv`, `rss_by_pid.log`, `progress.jsonl`, `driver.log`, `service.log` /
`engine_run.log`, pipe copies, `task_census.json` where applicable.

| prefix | track | status / note |
|---|---|---|
| `smoke/20260814T191343Z` | PDF | LG seq 10-doc smoke, pypdf |
| `blast/20260814T210610Z` | PDF | LG blast 150 × 3, Tika, uncapped |
| `rr/20260814T224936Z`, `rr/20260815T010908Z`, `rr/20260815T012817Z`, `rr/20260815T015052Z`, `rr/20260815T020130Z` | PDF | RR-only 200-doc blasts (pre-envelope, pre-patch); 015052Z is a re-report of 012817Z |
| `bench/20260816T060355Z` = `…_blast` | PDF | Run 8 (identical copies) |
| `bench/20260816T072711Z` = `…_c8` | PDF | Run 7 |
| `bench/20260816T074246Z` = `…_c12` | PDF | Run 6 |
| `bench/20260816T173915Z` = `…_blast` | PDF | Run 5 |
| `bench/20260816T224851Z` = `c128_1k_20260816T224851Z` | PDF | Run 4 (pre-patch baseline) |
| `bench/20260816T234710Z` = `c128_1k_PATCHED_20260816T234710Z` | PDF | Run 3 |
| `bench/20260817T023134Z` = `1k_final_blast_run` | PDF | Run 2 |
| `bench/20260817T041548Z` = `10k_native_saturation` | PDF | Run 1 |
| `bench/fault_20260817T072750Z_c8`, `bench/fault_20260817T075108Z_seq` | PDF | Runs 9, 10 (`fault_report.txt`, `fault_manifest.json`, per-arm rc/restart files) |
| `bench/20260818T043359Z_c128` | PDF | Run 12 (+ `CROSS_MODE_DETERMINISM.json`) |
| `bench/20260818T045211Z_seq` | PDF | Run 13 |
| `bench/20260818T052912Z_c128` | PDF | **Run 11, RUN PASS** (regenerated report + `CROSS_MODE_DETERMINISM.json`); `bench/20260818T052912Z` is the pre-regeneration copy |
| `bench/20260819T023248Z`, `…024149Z`, `…152537Z` | PDF | RR-only thread probes 256 (rejected) / 32 / 64 |
| `bench/20260908T223816Z`, `bench/20260908T224842Z` | PDF | PR #2197 stock / patched (run ids `pr2197pdf-{stock,patched}-20260908T223814Z`) |
| `bench/batch10k-2026-09-15/batch10k-b{16,32,64}-20260916T065635Z` | PDF | 10k docs in `send_files` batches of B on one connection, standard engine, RR only (§5.8); `sampler.runner.jsonl` + `sampler_merge.json` where the sampler was merged |
| `bench/batch10k-lg-2026-09-21/batch10k-lg-b{16,32,64}-20260921T075403Z` | PDF | the same three batch sizes on LangGraph, waves of B joined at a barrier (§5.8) |
| `bench/batch10k-lg-2026-09-21/batch10k-paired-b{16,32,64}-20260921T075403Z` | PDF | assembled cross-arm reports, one per batch size; `PAIRING.md` states what was copied from where |
| `bench/nodeprofile-2026-09-21/` | PDF | node-level attribution (§5.9): traced + control bulk cells and the per-document pathology run; traced cells carry `flow_events.jsonl` and `node_timings.json` |
| `videobench/nodeprofile-ami-2026-09-22/` | video | node-level attribution on AMI, both arms (§6.11): traced RR + LG on 24 videos, six videos sequentially on each arm, and the `analysis` dir with the self-time tables and the paired report |
| `bench/micro2197-20260909T071851Z` | PDF | `encode()` microbench (`out/{stock,patched}/results.json`, scripts, embeddings `.npy`, `chunks_1k.jsonl`); `…071314Z` = aborted first attempt (logs only) |
| `corpus/ami30h/`, `corpus/archive_films/`, `corpus/archive_films_v2/` | video | staged corpora (sha-pinned manifests) |
| `videobench/smoke-20260819T191632Z` | video | dual-lane smoke (`smoke_result.json`, `docs_rep{1,2}.json`) |
| `videobench/run30h-20260819T212432Z`, `…233913Z` | video | ENOSPC sizing runs (partial `per_doc.jsonl`) |
| `videobench/rundetect-20260820T012644Z`, `…062432Z` | video | detect-only 60 AMI, default / `threads=32` |
| `videobench/capture-20260820T054250Z` | video | ES2016d capture (`capture_docs.json`) |
| `videobench/s3test-20260820T184413Z` | video | S3 → RAM architecture test |
| `videobench/lgsmoke-20260821T000140Z` | video | LG arm smoke logs |
| `videobench/h2h-20260821T195300Z` | video | Run A |
| `videobench/native60-20260821T210828Z` | video | Run B |
| `videobench/native170-20260822T070136Z` | video | Run C (mirrored in `aws_videobench/results/` and `runs/ami-postures/`) |
| `videobench/archive10-20260822T190711Z` | video | films shakedown |
| `videobench/films10-20260823T170514Z`, `…20260824T030826Z`, `…20260825T060011Z` | video | films10 default (+ reruns) |
| `videobench/films50-20260823T175555Z` | video | films50 default |
| `videobench/films500-20260824T034901Z` | video | provenance only — LG OOM attempt |
| `videobench/films500-20260824T073256Z` | video | RR full leg (+ dead LG partial) |
| `videobench/films500-20260825T061529Z` | video | LG full leg, paired report (mirrored in `runs/films500-sizing/`) |
| `videobench/rrsweep-20260824T061609Z` | video | thread sweep (`sweep_summary.txt`, `threads_{unset,8,32,64}/`) |
| `videobench/matched8x4-ami-rep1-20260825T144717Z`, `…170702Z` | video | aborted matched smokes |
| `videobench/matched8x4-ami-rep1-20260825T181925Z` | video | matched smoke N=8 |
| `videobench/matched8x4-ami-rep1-20260826T064153Z` | video | RR matched 8×4 full |
| `videobench/matched8x4-ami-rep1-20260826T075823Z` | video | LG one-port 8 workers (paired with the RR above) |
| `videobench/matched6x4-ami-rep1-20260826T155110Z` | video | RR 6 × `threads=32` |
| `videobench/matched6x5-ami-rep1-20260826T172137Z` | video | equal-budget pair RR 6×5 / LG in-process 6×5 |
| `videobench/lgsweep-lg_{1proc_c32_t1 (×2), 1proc_c32_t2, 2proc_c32_t1, 4proc_c32_t1, 4proc_c48_t1, 8proc_c32_t1}-20260831T*` | video | LG topology sweep |
| `videobench/lgbest-lg_{4proc_c48_t1, 8proc_c32_t1, 4proc_c64_t1}-20260902T*` | video | LG best on AMI 168 |
| `videobench/rrbest-{16x2,32x1}-ami-20260902T131225Z` | video | RR best on AMI 168 |
| `videobench/filmsbest-50-20260903T040810Z` | video | AMI optima on films50 |
| `videobench/filmsbest-500-20260903T040810Z` | video | partial, stopped (provenance + task census only) |
| `videobench/rrfilms-{16x2,8x4}-20260903T202644Z` | video | RR films50 posture sweep |
| `videobench/films100-{rr16x2,lg4x48t1,lg8x48t1,lg4x48t2}-20260905T023331Z` | video | films100 tuned cells |
| `videobench/pr2197-{stock,patched}-20260907T024121Z` | video | PR #2197 video A/B |

Related but not ours: `s3://rocketride-benchmark-data/shashidhar/vfull170-0823/` (Shashi's
3-rep RR-vs-Haystack video run) and `ansh/batched_20260818T133821Z`,
`ansh/run10k_p2_blast_v2` (Ansh's batch-vs-per-doc pair referenced on #2053).

## Appendix B — where the source documents live

| document | content |
|---|---|
| `BENCHMARK_RUNS.md` | the 13 PDF runs of 16–18 Aug in narrative form (Series 1 and 2, fault runs, findings, gate evolution) |
| `aws_bench/RESULTS_PDF.md` | PR #2197 PDF A/B, environment, lines-of-code recount |
| `aws_bench/findings/rr_linux_boot.md` | the onnxruntime boot-pin finding |
| `ENGINE_BUG_REPORT.md`, `ENGINE_ISSUES_PRIORITY.md` | eight engine findings; the four prioritised issues RR-1..4 |
| `BENCHMARK_MEETING.md` / `benchmark_meeting.html` / `benchmark_results.html` | the PDF-track meeting deck |
| `ISSUE_2053_REPLY_DRAFT*.md`, `ISSUE_THROUGHPUT_CONFIG_DRAFT.md`, `ISSUE_VSCODE_MULTITASK_DRAFT.md` | issue texts (2053 posted; 2128 filed; VS Code draft unfiled) |
| `aws_videobench/RESULTS.md` | Runs A, B, C scoreboard and V0–V5 tables |
| `aws_videobench/RESULTS_AMI_FULL.md` | Run C narrative and workload character |
| `aws_videobench/RESULTS_AMI_POSTURES.md` | default vs matched cells on AMI, setups and every metric |
| `aws_videobench/RESULTS_FILMS.md` | the films campaign, phases 1–3, run index |
| `aws_videobench/MATCHED_POSTURE.md`, `RUN_MATCHED6x5_WALKTHROUGH.md` | posture definitions, config-by-config, smoke fixes, the 6×5 pair mechanics |
| `aws_videobench/findings/rocketride_cpu_utilization.md` | plain-language account of the single-task ceiling (pre-correction) |
| `aws_videobench/runs/` | committed raw bundles: `ami-postures/`, `films500-sizing/`, `rrsweep-20260823/` |
| `aws_videobench/METRICS.md`, `METRICS_EXPLAINED.md`, `ARCHIVE_FILMS*.md`, `DATA_FLOW_PLAN.md`, `SETUP_AND_RUN.md` | metric definitions, corpus provenance, runbooks |
| `VIDEO-FULLCORPUS-RESULTS-2026-08-24 (2).md` | Shashi's `vfull170-0823` results (RR vs Haystack, his harness) |
| `benchmark_video_*.html`, `~/Desktop/benchmark_video_exec.html` | video meeting pages |
| `Leela8256/aws_bench` (private GitHub) | reproduction package shared with the engine team for #2053 |

## Appendix C — adjacent work not folded in

Three bodies of measurement sit next to this ledger and are cited by it, but are not our
PDF-or-video pipeline runs, so their numbers stay out of the tables above.

**Cross-team PDF scoreboard (18 Aug, `BENCHMARK_MEETING.md`).** Three teams measured
RocketRide 3.3.1 (same two corrections) against a different framework each, on ~10,000
GovDocs1 PDFs per team and the same c7i.8xlarge class; RocketRide's cell is the range across
the three harnesses.

| metric (10k docs) | RocketRide | LangGraph (this ledger) | Haystack (Shashi) | LlamaIndex (Ansh) |
|---|---|---|---|---|
| chunks/s | 40.5–60.1 | 121.0 | **124.7** | 81.8 |
| CPU-s per document, loaded | 5.9–7.8 | 5.17 | **~3.5** | 4.51 |
| CPU-s per document, one at a time | 4.9–9.9 | 2.56 | **1.45** | 2.11 |
| CPU used, of allowance | 50–69% | **91%** | 72% | 79% |
| peak memory, loaded | **14.0–14.9 GB** | 17.7 GB | 21.1 GB | 17.6 GB |
| lines of code to a running service | **55–179** | 662 | ~129 | 557 |
| reports a corrupt file as an error | **no** | yes | yes | yes |

Haystack and LangGraph landing within 3% of each other on independent harnesses is the
cross-check that validates all three datasets; RocketRide's throughput and utilisation
findings were reproduced by every team.

**Shashi's video track** (`vfull170-0823`, RR vs Haystack, 3 reps) is summarised in §6.10;
**Ansh's WS-1** AMI cells (RR default, 16×2, 8×4; LlamaIndex 8×4) are quoted beside ours in
§6.5 and §6.7.

**RocketRide `model_server` on GPU (`modelserver_bench/`, from 1 Sep).** A separate sizing
study of the engine's model server (build 3.3.0.339) on the shared 8×H200 box
`rocketride-poc-1`, driving RT-DETR r50vd + MiniLM from the engine's own client path
(`--modelserver=localhost:5592`). Screening only — no comparison arm, single reps, shared
hardware:

| run | clients | frames/s (detect+embed pairs) | detect p50 (ms) | server CPU | reading |
|---|---|---|---|---|---|
| `matrix-20260902T232907Z` (synthetic frames) | 1 / 2 / 4 / 8 | 39.9 / 44.6 / 45.4 / 43.4 | 19.7 / 35.7 / 84.2 / 169.3 | — | ~45 fps plateau; one replica on GPU0 at ≤ 22% utilisation |
| `matrix-p5592-20260907T020619Z` (AMI-50 real frames, defaults) | 1 / 2 / 4 / 8 | 37.8 / 48.2 / 45.5 / 43.1 | 20.8 / 34.3 / 86.7 / 169.6 | 1.7 → 6.0 cores | GPU compute ≈ 5 ms per frame against 100–200 ms request latency: CPU pre-processing on the server is the bottleneck |
| posture screen, 7 Sep (`matrix-{defaults,shashi,wait0,workers8}-p5592-…`) | 4 and 8 | defaults 39.7 / 39.4 · Shashi's 256/4/4/64/25 −3.7% / −3.9% · `batch-wait 0` −5.5% / **+3.6%** · `workers 8` −4.5% / **−20.1%** | 95–236 | 6.9–14.4 cores | no posture moves the plateau; `workers 8` hurts at 8 clients |

Plan and open items live in `modelserver_bench/PLAN_MODELSERVER_STUDIES.md`; the
confirmation phase (3 reps, 10-minute windows, idle-GPU gate) has not been run.

---

*Compiled 9 September 2026 on branch `aws-bench` (HEAD `43d46d5`, working tree dirty with
the uncommitted PR #2197 harness); revised the same evening — every cell cross-checked
against the committed results documents and the S3 report extracts (`report.txt` /
`RUN_REPORT.json` of every bundle), all matching except Run 12's LangGraph effective cores
(25.99 in the raw report; the 24.97 in `BENCHMARK_RUNS.md` was a transcription slip, now
fixed there too). §5.8 added 16 September 2026 and completed 21 September
with the LangGraph arm and the paired cross-arm reports (harness commits `15368a9`,
`db0487b`, `a1537bf`, `80b838d`, `9520e9b`, `06aa315`, `6e55aee`, `0490110`).*