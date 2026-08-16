# aws_bench — LangGraph vs RocketRide, on the AWS x86_64 box

Self-contained. Everything needed to build both arms, run a matched
benchmark, and derive metrics lives in this folder. Nothing outside it is
required at run time.

```
aws_bench/
  docker-compose.yml        both arms + tika, ONE equal envelope
  arms/langgraph/           service, graph, workload, Dockerfile, tika-config
  arms/rocketride/          Dockerfile, entrypoint, probe fixtures
  pipe/benchmark_pdf.pipe   the shared pipeline contract (both arms run THIS)
  metrics/                  canonical metric logic — nothing computed elsewhere
  bench/                    drivers, sampler, report
  corpus/fetch_govdocs.sh   corpus, downloaded on the box
  run/matched_run.sh        the run
  local/box.sh              Mac-side EC2/SSM control
  findings/                 product issues found while benchmarking
  results/                  run output (git-ignored)
```

## The one rule

**Only the framework may vary between arms.** Same corpus, same N, same
document order, same envelope, same rep count, same mode, same warm-start.
`run/matched_run.sh` enforces this and records every one of them in
`provenance.json`; a run whose provenance is incomplete is not publishable.

The runs before this folder existed violated it — LangGraph at 150 docs
capped, RocketRide at 200 docs uncapped — and were therefore not comparable.
That is the reason this folder exists.

## Run it

On the box (see `local/box.sh` for getting there):

```bash
git clone <repo> && cd <repo>/aws_bench
bash run/preflight.sh                     # x86_64, cores, docker, disk, curl
bash run/install_awscli.sh                # Ubuntu AMI ships no aws cli, no sudo
bash corpus/fetch_govdocs.sh ~/bench_corpus 200
bash run/matched_run.sh                   # blast, 200 docs, 3 reps, both arms
```

Knobs, all recorded in provenance:

| var | default | |
|---|---|---|
| `N` | 200 | documents |
| `REPS` | 3 | repetitions per arm — 3 is the minimum for a CV |
| `MODE` | `blast` | or `c8` for closed-loop with 8 in flight |
| `WARM` | 25 | warm-start docs, timed separately and excluded |
| `ARM_CPUS` / `ARM_MEM` | `12.0` / `10g` | applied to BOTH arms |
| `LG_EXTRACTOR` | `tika` | matches RocketRide's parser |
| `RR_THREADS` | unset | unset = engine default pool |

Long runs must not sit in an SSM session:
`nohup bash run/matched_run.sh > ~/logs/run.log 2>&1 < /dev/null &`

## Reading the output

`results/<stamp>_<mode>/` holds raw per-doc records, sampler streams,
`provenance.json` and `report.txt`, and is uploaded to S3. Every metric is
re-derivable from the raw records forever:

```bash
aws s3 cp s3://.../<stamp>/ ./run --recursive --profile leela
python3 bench/report.py ./run          # same numbers, on your laptop
```

## What to look at, and what not to quote

- **`cpu_s_per_chunk`** is the soundest single number. It is work-normalised
  and tail-robust (moved 0.373 → 0.435 when the two heaviest documents were
  removed from a 200-doc run — barely).
- **`chunks_per_s` beside `docs_per_s`, never `docs_per_s` alone.** A document
  is not a fixed unit of work.
- **Latency labels are not interchangeable.** Closed-loop gives service
  latency; blast gives batch-position latency, which includes queue wait.
- **`INSUFFICIENT_REPS` or `UNSTABLE` means do not quote a point value.**
- **Utilisation is span-averaged**, so one slow document drags it down. On our
  govdocs corpus the largest document holds 41% of all chunks and a single
  worker processes it, which pinned average parallelism near 6 cores while the
  parallel phase ran at ~61 chunks/s. Read the distribution, not just the mean.

## Known, and deliberate

- The **tika sidecar is where LangGraph parses** in tika mode, so its CPU is
  not in the langgraph cgroup. It is sampled separately and reported
  alongside — never merged.
- `/meta` reports `executor_workers: 4`, which is **inert**: the graph uses
  LangGraph's default executor, width `min(32, cpu_count+4)`, and `cpu_count()`
  sees host cores rather than the cgroup quota.
- The RocketRide image **patches a broken dependency pin** so the engine can
  boot on Linux at all — see `findings/rr_linux_boot.md`. Recorded in
  provenance; remove when upstream fixes it.
- **`000164.pdf` and `000357.pdf` return zero chunks on BOTH arms.** Only the
  first is allowlisted, so the M0 gate fails closed on the second. It should
  be reclassified as a no-text document, not treated as an engine defect.
