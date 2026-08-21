# aws_videobench — the video benchmark (RocketRide vs LangGraph)

Self-contained: both arms, the shared pipeline contracts, the corpus
tooling, the drivers, and the canonical metric/gate logic all live here.
Nothing outside this folder is required to benchmark the video workload
(the only external piece is `aws_bench/local/box.sh` for laptop-side EC2
control).

```
aws_videobench/
  DATA_FLOW_PLAN.md          the architecture, end to end — READ FIRST
  METRICS.md                 the V0–V5 metric specification
  docker-compose.yml         both arms + the client, one file
  pipe/
    benchmark_video_detect.pipe   THE benchmark contract (frames 15s →
                                  RF-DETR 0.3 → split 4000 → miniLM 384)
    benchmark_video.pipe          dual-lane variant (adds Whisper — carries
                                  the known determinism caveat)
  arms/
    rocketride/              engine 3.3.1, SHA-pinned + boot fix +
                             duplication patch ("3.3.1 with the documented
                             duplication correction", never "stock")
    langgraph/               FastAPI + StateGraph mirror of the pipe,
                             stage-for-stage (own Dockerfile + local smoke)
  corpus/
    fetch_ami.sh             mirror → mux → run-ready .avi (+manifest, SHAs)
    sets/ami_full.txt        all 170 usable AMI meetings (99.5 h)
    sets/ami30h.txt          the 62-meeting sizing set
  bench/                     canonical logic — nothing computed elsewhere
    bench_video.py           RocketRide driver (blast / seq / c<N>)
    lg_driver.py             LangGraph driver (seq / c<N>) — SAME record schema
    capture_one.py           full-output capture for verification passes
    report.py                gates first, numbers second, fail-closed
    metrics/v0_gates.py      census, structure, frame_law, self_duplication,
                             determinism, cross-arm equivalence bands
    metrics/v_metrics.py     V1 throughput, V2 latency, V3 efficiency,
                             V4 resources, V5 cost
  run/                       on-box entrypoints (each self-documenting)
    stage_corpus.sh          one-time: full corpus → S3 (delete-as-you-go)
    run_waves.sh             the campaign runner (S3 → RAM → engine → S3)
    run_detect.sh / run_s3test.sh / smoke_lg_box.sh / capture_one.sh / smoke_run.sh
  smoke/                     client image + 1-video smoke driver
  results/                   run output (git-ignored; S3 is the record)
```

## The one rule

**Only the framework may vary between arms.** Same corpus, same order,
same envelope, same reps, same mode, same deadline, same client. One arm
at a time, one engine per arm. Provenance or it didn't happen.

## Run it (on the box)

```bash
cd ~/bench_langgraph_prod/aws_videobench
bash run/stage_corpus.sh                  # once: 170 meetings → S3 (~1.5 h)
TOTAL=170 W=85 CORPUS_MODE=s3 \
  S3_CORPUS=s3://rocketride-benchmark-data/leela/corpus/ami_full \
  nohup bash run/run_waves.sh > ~/logs/waves.log 2>&1 < /dev/null &
```

LangGraph runs through the same compose (`docker compose up -d langgraph`,
then `bench/lg_driver.py` from the smoke container) — never at the same
time as the RocketRide arm in a measured run.

## Read the results

Every metric is re-derivable from raw records forever:

```bash
aws s3 cp --recursive s3://rocketride-benchmark-data/leela/videobench/<run>/ ./run --profile leela
python3 bench/report.py ./run                       # one run: gates + V1–V5
python3 bench/report.py ./rep1 ./rep2 ./rep3        # reps: adds determinism
python3 bench/report.py --arms ./rr1,./rr2 ./lg1,./lg2   # cross-arm gates
```

`report.py` exits non-zero on any hard gate failure; single-rep runs are
labeled sizing evidence and cannot pass determinism. See METRICS.md for
what each gate and number means, and DATA_FLOW_PLAN.md §8 for the traps
that have already bitten once.
