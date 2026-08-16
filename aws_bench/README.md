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
  bench/                    CLIENT container + drivers, sampler, report
  corpus/fetch_govdocs.sh   corpus, downloaded on the box
  run/matched_run.sh        the run
  local/box.sh              Mac-side EC2/SSM control
  findings/                 product issues found while benchmarking
  results/                  run output (git-ignored)
```

## The one rule

**Only the framework may vary between arms.** Same corpus, same N, same
document order, same envelope, same rep count, same mode, same warm-start,
same deadline, and the same client driving both.
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
bash corpus/fetch_govdocs.sh 200          # -> ~/bench_corpus_200
bash run/matched_run.sh                   # blast, 200 docs, 3 reps, both arms
```

Knobs, all recorded in provenance:

| var | default | |
|---|---|---|
| `N` | 200 | documents |
| `REPS` | 3 | repetitions per arm — 3 is the minimum for a CV |
| `MODE` | `blast` | or `c8` for closed-loop with 8 in flight |
| `WARM` | 25 | warm-start docs, timed separately and excluded |
| `ARM_CPUS` | 3/4 of host | cores per ARM; the client gets the rest |
| memory | uncapped | measured, not enforced — see below |
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
- **`cpu_utilization` is against the ARM'S ALLOCATION, not the host.** An arm
  capped at 12 on a 32-core box can never exceed 0.375 of the host, so the
  host figure reads as "idle" when the arm is a third saturated — the same
  LangGraph run is 33.4% of its allocation and 12.5% of the machine.
  `cpu_utilization_host` is reported beside it for capacity questions.
- **Utilisation is span-averaged**, so one slow document drags it down. On our
  govdocs corpus the largest document holds 41% of all chunks and a single
  worker processes it, which pinned average parallelism near 6 cores while the
  parallel phase ran at ~61 chunks/s. Read the distribution, not just the mean.

## The correctness gate

A run is PASS only if every arm passes M0 **and** the two arms are proven to
have done the same work. `bench/report.py` exits non-zero otherwise.

Per arm, fail-closed — a missing field or unproven state is a violation, never
a default pass:

| check | catches |
|---|---|
| `census` | loss: offered == records, no duplicates, no unexpected failures, missing docs NAMED against the manifest |
| `structure` | corruption: per-arm field contract exactly True (LG: identity + `X-Output-SHA256` body hash + finite vectors; RR: filepath identity), >=1 chunk, 384 dims, L2 norm within 1e-3 of 1.0, hash count == chunk count |
| `determinism` | instability: ordered chunk hashes identical across reps; an outcome that flips also fails |

Across arms — the check that makes the comparison mean anything:

| check | rule |
|---|---|
| chunk ratio, **hard** 0.4-2.5 | outside it a whole document was dropped or duplicated. **FAILS.** |
| chunk ratio, **warn** 0.8-1.25 | real workload asymmetry. Reported; this is why `chunks_per_s` is published beside `docs_per_s`. Does not fail. |
| byte parity | ordered chunk hashes identical per doc. Always measured; **gated only when both arms parse with Tika**, since with different extractors identical hashes are not expected. |

Why it is not ceremony: on a previous 49-doc run the median chunk delta was 0
but the max was **+89 chunks and a 1.977x char ratio** — one arm nearly doubled
the other's work on a document, while both were individually valid. A
`parity_fixture` run separately showed embeddings agree to 1.3e-07 given
identical text, so divergence enters at extraction/chunking, not embedding.

Single-rep runs **cannot** pass: with no second observation determinism is
unproven, and unproven fails closed.

## Why there is a third container

`bench-client` runs BOTH drivers and reaches each arm over the network. This
is not incidental — it fixes two asymmetries that invalidated earlier runs:

- **Client cost.** The RocketRide driver used to run *inside* the engine's
  container, so its SDK reads, WebSocket framing and JSON deserialization were
  charged to RocketRide's cgroup, while LangGraph's driver ran on the host and
  was charged nothing. Now neither arm pays for the client.
- **Transfer path.** RocketRide used to read container-local disk while
  LangGraph paid for an HTTP upload inside its measured latency. Now both
  arms receive their bytes over an equivalent network hop.

The client is pinned to its own cores (`CLIENT_CPUSET`, the cores left over
after `ARM_CPUS`) so it can never steal from the arm it is measuring.

## Envelope

- **CPU**: every container in an arm shares one `BENCH_CPUSET`. LangGraph's
  arm is langgraph+tika *together*; RocketRide's is one container. Without
  this the LG arm silently gets a second full allocation.
- **Memory**: deliberately **not capped**. Capping per container gave the
  two-container LG arm twice RocketRide's ceiling, an arm-total cap cannot be
  expressed per container, and a 10g cap would have OOM-killed RocketRide
  (measured peak 10,536 MB). Memory *granted* must be equal — unconstrained on
  both sides is equal — and memory *used* is a reported outcome (peak RSS).
- **Deadline**: one `BENCH_TIMEOUT_S` for both arms and both modes. Previously
  LangGraph had 300 s per request while a RocketRide batch had 3600 s, so a
  healthy LangGraph document could be killed by queue wait while the
  equivalent RocketRide batch ran on for an hour.
- **Tika is stopped before RocketRide starts**, so an idle JVM is not sitting
  on the arm's cores holding memory during RR's measurement.

## Known, and deliberate

- **An "arm" is the service plus whatever it offloads to.** In tika mode
  LangGraph parses in the sidecar, so its parse CPU is in a different cgroup
  and it would otherwise get a second full CPU allocation. Both are handled:
  every container in an arm shares one `BENCH_CPUSET` (so LangGraph+tika get
  the same cores RocketRide gets alone), and the report charges tika's CPU to
  LangGraph as `m7_arm_total`, which is what feeds `cpu_s_per_chunk`.
  Service-only stays available as `m7_efficiency_service_only`.
  RocketRide's Tika is embedded in its engine, so it was always charged for
  parsing — anything else compares a parsing framework to a non-parsing one.
- **Tika time IS in LangGraph's latency and throughput** and always was: the
  extract node makes a blocking HTTP call inside the request the driver times.
- `/meta` reports `executor_workers: 4`, which is **inert**: the graph uses
  LangGraph's default executor, width `min(32, cpu_count+4)`, and `cpu_count()`
  sees host cores rather than the cgroup quota.
- The RocketRide image **patches a broken dependency pin** so the engine can
  boot on Linux at all — see `findings/rr_linux_boot.md`. Recorded in
  provenance; remove when upstream fixes it.
- **`000164.pdf` and `000357.pdf` return zero chunks on BOTH arms.** Only the
  first is allowlisted, so the M0 gate fails closed on the second. It should
  be reclassified as a no-text document, not treated as an engine defect.
