# DATA_FLOW_PLAN.md — Video benchmark on the full AMI Meeting Corpus

The end-to-end architecture and runbook for benchmarking **RocketRide vs
LangGraph** on the **entire AMI Meeting Corpus** (171 meetings, 100.2 h of
footage, 170 usable by our pipeline). Written so a teammate with AWS
access and this repo can execute the whole campaign without other context.

Two run shapes are documented, both already validated at small scale:

- **Shape 1 — "one-go" smoke/sizing run**: everything in a single batch.
  Use it to validate a change, size a machine, or smoke a new arm.
- **Shape 2 — wave benchmark**: the full-corpus campaign. Corpus lives in
  S3, waves stage through RAM, engines are torn down between waves.

Read alongside: `METRICS.md` (what we measure, the V0–V5 suite),
`README.md` (folder layout), `aws_bench/README.md` (the envelope
discipline this inherits).

---

## 0. The one rule (inherited from the PDF benchmark)

**Only the framework may vary between arms.** Same corpus, same N, same
document order, same envelope, same rep count, same mode, same deadline,
same client driving both. Every knob is recorded in `provenance.json`; a
run with incomplete provenance is not publishable. One arm runs at a time.

## 1. The pipeline under test (both arms, stage-for-stage)

```
              VIDEO (muxed AMI meeting, .avi with audio track)
                 │
   ┌─────────────┴─────────────┐
   │ RocketRide 3.3.1 engine   │   LangGraph arm (FastAPI + StateGraph)
   │ pipe/benchmark_video_     │   arms/langgraph/
   │ detect.pipe               │
   ├───────────────────────────┼──────────────────────────────────────┐
   │ webhook (video lane)      │  POST /process (multipart upload)    │ ingest
   │ frame_grabber interval=15 │  ffmpeg -vf fps=1/15 (lossless PNG)  │ 1 frame / 15 s
   │ detect rfdetr thr=0.3     │  rfdetr RFDETRBase thr=0.3           │ per-frame JSON line
   │ preprocessor_langchain    │  RecursiveCharacterTextSplitter      │ 4000 chars, no overlap
   │ embedding_transformer     │  sentence-transformers               │ multi-qa-MiniLM-L6-cos-v1
   │   profile miniLM          │  (the model miniLM actually is —     │ 384-dim, L2-normalized
   │                           │   NOT all-MiniLM-L6-v2)              │
   │ response_documents        │  documents JSON payload              │ chunks + vectors back
   └───────────────────────────┴──────────────────────────────────────┘
```

Splitter parameters and the detection JSON format were recovered
byte-exactly from engine output (see git history), so cross-arm output
differences measure model/decode behavior, not formatting noise. Byte
parity is **not** required (decision 2026-08-20); the V0 cross-arm bands
in METRICS.md are the equivalence contract.

## 2. Corpus data flow — from the AMI mirror to S3 (one-time staging)

The AMI mirror ships video and audio **separately** (the .avi camera files
contain no audio track), so every corpus document is built by muxing:

```
groups.inf.ed.ac.uk/ami/AMICorpusMirror  (~7–8 MB/s, be polite)
      │  <mtg>.Closeup1.avi  (video only)  +  <mtg>.Mix-Headset.wav (16 kHz PCM)
      ▼
  BOX (EBS)   corpus/fetch_ami.sh
      │  ffmpeg stream-copy mux → <mtg>.avi   (bitexact, no re-encode)
      │  corpus_manifest.json (per-video duration_s) + SHA256SUMS
      ▼
  S3          s3://rocketride-benchmark-data/leela/corpus/<set>/
              *.avi + corpus_manifest.json      ← the CANONICAL corpus home
      │
      └─ EBS copy and raw download cache are DELETED after verified upload.
         S3 is the only copy; everything downstream pulls from S3.
```

- Full-corpus numbers: 170 muxed videos ≈ **24 GB** in S3; staging is
  mirror-bound (~1.5–2 h one-time). S3→box later runs at ~158 MB/s
  (measured), so re-pulls are seconds-to-minutes.
- **Prerequisite task**: `fetch_ami.sh`'s candidate list currently covers
  scenario meetings only (ES/IS/TS). The full corpus needs EN/IB/IN added
  to the generated list (~135 → 170 usable; all six >1 h meetings are
  EN/IN). TS3003d is skipped everywhere: no Closeup1 on the mirror.
- Permissions this relies on (all verified): box role writes AND reads
  the bucket; laptop `leela` SSO profile reads results.

## 3. Shape 1 — the "one-go" run (smoke / sizing)

Everything in one batch against one arm. This is what every run so far
has been. Data flow:

```
S3 corpus ──(aws s3 cp, ~158 MB/s)──► /dev/shm (RAM tmpfs, ≤~30 GB)
                                          │ bind-mounted read-only
                                          ▼
   client container (bench_video.py) ──ONE send_files batch / N POSTs──►
                                          ▼
   arm container (RR engine  or  LG service)
        │  RR: accumulates upload scratch on EBS (container lifetime!)
        │  LG: tempfile per request, deleted immediately
        ▼
   results dir  ──(s3 sync every 60 s, live)──►  S3 results prefix
        ▼
   docker compose down  → releases RR scratch;  rm -rf /dev/shm slice
```

Commands (on the box, from `aws_videobench/`):

```bash
# RocketRide, 30–60 videos, one blast (see run_s3test.sh / run_detect.sh):
nohup bash run/run_s3test.sh   > ~/logs/s3test.log  2>&1 < /dev/null &
# LangGraph, a few videos through the service (see smoke_lg_box.sh):
nohup bash run/smoke_lg_box.sh > ~/logs/lgsmoke.log 2>&1 < /dev/null &
```

When to use: validating a pipe/arm change, first run on new hardware,
sizing questions. Single-rep one-go runs **cannot pass the determinism
gate** — they are sizing evidence, never benchmark results (METRICS.md).

## 4. Shape 2 — the wave benchmark (full AMI corpus)

The campaign shape. Why waves exist — two measured constraints:

1. **RocketRide retains every uploaded video in engine scratch for the
   container's lifetime** (survives batch completion AND `terminate()`;
   released only by container removal). Unbounded runs therefore fill any
   disk — the 2026-08-19 ENOSPC failure. Teardown-per-wave is the broom.
2. RAM staging (`/dev/shm`, ~30 GB) holds one wave, not the corpus.

```
                        ┌─────────────  per wave (W ≈ 60–100 videos)  ─────────────┐
                        │                                                          │
S3 corpus  ──s3 cp──►  /dev/shm/wave  ──LPT shard by size──►  shard_1 … shard_K    │
(24 GB, canonical)      (RAM, ~8–14 GB)                          │                 │
                        │                                        ▼                 │
                        │                    K engine containers (RR)  — or —      │
                        │                    the LG service container              │
                        │                    boot → healthcheck-gated              │
                        │                         │                                │
                        │       K parallel client containers, one per shard        │
                        │       (bench_video.py blast / LG driver POSTs)           │
                        │                         │                                │
                        │       per-video progress.jsonl + cgroup sampler          │
                        │                         │                                │
                        │       results ──s3 sync every 60 s──► S3 (live)          │
                        │                         ▼                                │
                        │       docker compose down   ← releases RR scratch        │
                        │       rm /dev/shm/wave      ← releases RAM               │
                        │       wave marked done in waves_done (synced to S3)      │
                        └──────────────────────────────────────────────────────────┘
                                     repeat until corpus exhausted; RESUMABLE —
                                     a crash costs one wave, never the campaign
```

Implementation: `run/run_waves.sh` + `run/gen_engines.py` (K engines, LPT
sharding, resume file, S3-GET preflight). Two adaptations are open work
items before the full campaign (small, listed in §7): pulling waves to
RAM instead of EBS inside run_waves.sh, and a LangGraph driver that
emits the same per_doc.jsonl record schema.

### Matched-benchmark configuration (what makes it publishable)

- **One arm at a time** under the equal envelope: shared `BENCH_CPUSET`
  for everything in an arm, intra-op threads pinned
  (`OMP_NUM_THREADS=1`), one `BENCH_TIMEOUT_S`, memory uncapped-but-
  measured, client on its own cores. Copy the discipline from
  `aws_bench/docker-compose.yml` verbatim.
- **3 reps minimum** per arm per mode (determinism gate needs ≥2; CV
  needs 3). Modes: `seq` (service latency), `c8` (bounded concurrency),
  `blast`/`native_saturation` (each arm's native ingestion path — see
  aws_bench README for why forcing one interface on both misbenchmarks).
- **K engines is RocketRide's scale-out story, reported separately** from
  the single-service matched comparison: the ~6-core-per-engine ceiling
  is a product property (threads=32 changed nothing); K=5 sharding is
  the deployment mitigation. Never blend the two configurations.
- Gates and metrics: METRICS.md V0–V5, fail-closed. Full-corpus scale:
  ~100 h footage → RR single-engine rep ≈ 2.5–3 h; LG rep sized by its
  first pinned run; whole campaign comfortably inside two box-days.

## 5. Where everything lives

| thing | location |
|---|---|
| canonical corpus | `s3://rocketride-benchmark-data/leela/corpus/<set>/` (+manifest) |
| results (live-synced) | `s3://rocketride-benchmark-data/leela/videobench/<run>-<stamp>/` |
| pipe contracts | `aws_videobench/pipe/*.pipe` (detect is the benchmark pipe) |
| RR engine image | `aws_videobench/engine/Dockerfile` — 3.3.1 SHA-pinned + boot fix + dup patch; describe as "3.3.1 with the documented duplication correction", never stock |
| LG arm | `aws_videobench/arms/langgraph/` (service + Dockerfile) |
| drivers | `aws_videobench/bench/bench_video.py` (records, event times), `capture_one.py` (full-output verification) |
| run scripts | `aws_videobench/run/*.sh` — each is self-documenting |
| box control (laptop) | `aws_bench/local/box.sh` — start/stop/run/launch/tail |
| model caches | docker volume `rr-model-cache` (shared by RR engines AND the LG container) — keep; it is why nothing re-downloads |

## 6. Known traps (every one of these has bitten already)

1. **SSM sessions need a pty** since agent 3.3.4793.0 — box.sh handles it
   (`script -q /dev/null`); zombie "Connected" sessions pile to the
   25-per-instance cap and lock the box; only stop/start clears them.
2. **`aws` vanishes from PATH in nohup shells** — scripts resolve
   `~/.local/bin/aws` explicitly; keep that pattern.
3. **RR scratch retention** (§4). Never plan disk as if uploads free
   themselves. Teardown between waves is load-bearing, not hygiene.
4. **Delete the raw AMI cache after muxing** — it is re-downloadable; the
   ENOSPC run was caused by keeping it.
5. **SSO tokens expire within hours**; `aws sso login --profile leela`
   (browser approval). The BOX role never expires — long runs are safe;
   only laptop-side polling/downloads are affected.
6. **The box auto-stops when idle** — long runs must be `nohup`-detached
   (they survive), and results are safe because of the 60 s live sync.
7. **`miniLM` ≠ all-MiniLM-L6-v2.** It is multi-qa-MiniLM-L6-cos-v1.
   Vectors from the wrong model pass every dimension/norm check and are
   silently incomparable.
8. **Detection-dense rooms (IB/IS ≈ 16 objects/frame) exceed 4000 chars
   per frame-line**, so the splitter cuts mid-line and chunks are not
   valid JSON — in BOTH arms, faithfully. Expect chunks > frames there.
9. **Whisper (dual-lane pipe only)**: temperature-fallback flips ~1
   segment per 20 min across reps → determinism gate fails. Detect pipe
   is unaffected. Decide patch-vs-soft-gate before dual-lane reps count.
10. **Cross-platform outputs differ ±1%** (ffmpeg/torch numerics); all
    gates are within-platform, all comparisons box-vs-box.

## 7. Open work items before the full-corpus campaign

| item | size |
|---|---|
| extend fetch_ami.sh candidate list to EN/IB/IN (full 170) | small |
| stage full corpus to S3, verify counts+SHAs, delete EBS copy | one command + ~2 h mirror time |
| run_waves.sh: pull waves to /dev/shm (currently EBS) + byte-capped waves | small |
| LG bench driver emitting per_doc.jsonl schema (POST loop, hashes, timings) | medium |
| envelope wiring for video (BENCH_CPUSET, OMP=1, one deadline, client cores) | port from aws_bench |
| per-video detection counts in driver records (V0 detection_ratio gate) | one line |
| 2-engine sharding probe (validates the K-engine claim) | 30 min box time |

## 8. Minimal teammate quickstart

```bash
# laptop (needs: aws cli + session-manager-plugin + SSO access, repo clone)
aws sso login --profile leela
bash aws_bench/local/box.sh start
bash aws_bench/local/box.sh run 'cd ~/bench_langgraph_prod && git pull --ff-only origin aws-bench'
bash aws_bench/local/box.sh launch <name> 'cd ~/bench_langgraph_prod/aws_videobench && bash run/<script>.sh'
bash aws_bench/local/box.sh tail <name>          # poll progress
# results stream to S3 continuously; analyze from any machine:
aws s3 cp --recursive s3://rocketride-benchmark-data/leela/videobench/<run>/ ./run --profile leela
bash aws_bench/local/box.sh stop                 # ALWAYS stop when done
```
