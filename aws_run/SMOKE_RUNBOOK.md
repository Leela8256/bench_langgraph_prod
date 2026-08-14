# Smoke test on the AWS box — 10 documents through LangGraph

**Goal:** prove the whole chain works on the native x86_64 box — image builds
non-emulated, service boots, documents process, and `metrics/` derives
M0/M1/M2/M7 from the raw records. LangGraph arm only; the RocketRide arm waits
on the registry pull.

**What this is NOT:** a performance result. 10 documents cannot support the
20/25-completion warm-start rule (`metrics/README` M1), so throughput here
includes cold-cache effects. Treat every timing as a smoke signal.

## Relationship to `aws/smoke.sh`

`aws/smoke.sh` (already on `main`) is the **liveness** smoke: 5 govdocs PDFs
via curl, checking HTTP 200, the `X-Output-SHA256` header, chunk count and
vector dim, then uploading to
`s3://rocketride-benchmark-data/leela/smoke/`. It answers "is the box and the
service alive?" and it is the faster thing to run first.

This runbook is the **measurement** smoke: it emits `per_doc.jsonl` in the
schema `metrics/` consumes, so M0/M1/M2/M7 are derived by the canonical
modules rather than by eyeballing curl output. Run `aws/smoke.sh` first; run
this when you want the metric path itself proven. Both use the same corpus
source, so their documents are the same bytes.

## Why it is shaped this way

| Choice | Reason |
|---|---|
| **Two passes over the same 10 docs** | M0 fails closed. `determinism()` needs a second observation; one pass leaves the gate incomplete, and an incomplete gate is a FAIL, not a pass. Pass 2 is unsampled — it only contributes chunk hashes. |
| **Sequential, not blast** | Closed-loop gives *true service latency* (`metrics/README` M2). A blast run yields batch-position latency including queue wait, and the two must never be compared. |
| **Sampler wraps pass 1 only** | So M7's CPU window matches M1's throughput window. Sampling both passes would divide pass-1 documents by two passes' CPU. |
| **New `cgroup_sampler.py`** | `pdf1k/proc_sampler.py` derives `cpu_total_s` from `/proc/stat`, which is not namespaced in Docker and whose fields include `idle` — so it reports ~n_host_cpus regardless of load. That is why `GATE50_REPORT.json` shows langgraph `avg_cores: 17.81` on an 18-vCPU VM while the container was capped at 12 CPUs. The new sampler reads the container's own cgroup accounting. |
| **`pip freeze` captured from the built image** | The Dockerfile pins only `pypdf==6.15.0`; the other six deps are `>=`. `langchain-text-splitters` decides chunk boundaries and `sentence-transformers` decides vectors, so an unpinned resolve can silently change output bytes. This records what the box actually resolved, which is the input to the pinning decision. |
| **Corpus fetched on the box** | There is no scp path. The first 10 govdocs PDFs from the same digitalcorpora zip `aws/smoke.sh` already uses, selected by the same `sorted(*.pdf)[:N]` rule as gate-50, SHA-256 recorded. Matching the corpus is what makes box numbers comparable to Mac numbers. |

---

## Step 1 — Publish the code (LOCAL, before touching AWS)

The box can only get code by cloning the public repo, so anything not pushed
does not exist as far as the box is concerned.

```bash
cd "/Users/leelaprasaddammalapati/Desktop/prod bench"
git add aws_run metrics gate50
git commit -m "aws_run: smoke test package; cgroup-based sampler (proc_sampler /proc/stat counted host idle)"
git push origin main
# then refresh the public mirror however you normally do it, e.g.:
#   git push public-main:main       (or your curated-mirror procedure)
```

Verify the public repo has everything the box needs: `metrics/`,
`langgraph-fastapi/`, `docker-compose.yml`, `aws_run/`.

## Step 2 — Start the box (LOCAL)

```bash
aws sso login --profile leela
aws ec2 start-instances --instance-ids i-0bdc8b1e18f2a5348 --region us-east-1 --profile leela
aws ssm start-session --target i-0bdc8b1e18f2a5348 --region us-east-1 --profile leela
```

> The box auto-stops after 1 h below 20 % CPU with no warning. The build is the
> slow part (~10 min: pip install + baking the embedding model). Stay in the
> session while it runs.

## Step 3 — On the box

```bash
cd ~
git clone https://github.com/Leela8256/bench_langgraph_prod.git
cd bench_langgraph_prod
bash aws_run/preflight.sh                    # expect x86_64, 32 cores
bash aws_run/box/fetch_smoke_corpus.sh       # 10 arXiv PDFs -> ~/smoke_corpus
bash aws_run/box/smoke10.sh                  # build -> boot -> 20 docs -> metrics
```

`smoke10.sh` exits 0 only when the M0 gate is green. To use the Tika sidecar
instead of pypdf: `LG_EXTRACTOR=tika bash aws_run/box/smoke10.sh`.

## Step 4 — Read the result

Everything lands in `aws_run/evidence/smoke_<UTC-stamp>/`:

| File | What it proves |
|---|---|
| `environment.txt` | arch, cores, RAM, docker/compose versions, git SHA, whether the tree was dirty |
| `image_id.txt` | image digest + architecture — the native-build receipt |
| `pip_freeze.txt` | what the unpinned `>=` ranges actually resolved to |
| `meta.json` | live `/meta`: extractor, embedding model, split config, reported executor width |
| `corpus.sha256` | exact input bytes |
| `pass1/per_doc.jsonl` | raw per-document records — every metric is re-derivable from this forever |
| `pass1/sampler.jsonl` | resource stream (first line records which CPU/RSS source was used) |
| `pass2/per_doc.jsonl` | second observation for determinism |
| `SMOKE_REPORT.json`, `report.txt` | the derived M0/M1/M2/M7 |

Paste `report.txt` back and I will analyse it.

## Step 5 — Stop the box

```bash
aws ec2 stop-instances --instance-ids i-0bdc8b1e18f2a5348 --region us-east-1 --profile leela
```

(Disk survives. Results stay on the box; S3 exfil is a separate step, not
needed for a smoke test.)

---

## What to look at first in the output

1. **`m0_PASS`** — must be `true`. If false, read `census` (documents lost) and
   `structure` (contract violated: `identity_ok` / `sha_header_ok` /
   `vectors_finite` / 384 dims / L2 norm ≈ 1 / hash count).
2. **`m7_sanity.effective_cores`** — must be **≤ 12** (the container's CPU
   limit). If it comes back near 32, the sampler is still attributing host CPU
   and M7 is invalid. This is your teammate's `cpu_utilization ≤ 1.0` gate,
   adopted.
3. **`determinism.PASS`** — the same 10 documents must produce byte-identical
   ordered chunk hashes across both passes.
4. **`pip_freeze.txt`** — compare `langchain-text-splitters` and
   `sentence-transformers` against the Mac image. If they differ, chunk
   boundaries and vectors may differ too, and the Dockerfile needs full pins
   before any real run.

## Known caveats, stated up front

- **Resource envelope is Mac-tuned.** `docker-compose.yml` caps each arm at
  12 CPU / 10 GB, chosen for an 18-core Mac. On a 32-vCPU box that is a
  deliberate under-allocation for this smoke test; the real envelope must be
  recomputed and pre-registered before any measured run.
- **`executor_workers` in `/meta` is a lie by construction.** It reports the
  configured `4`, but `pipelines/document_pdf/nodes.py` uses LangGraph's
  default executor, whose width is `min(32, os.cpu_count() + 4)`. `cpu_count()`
  sees host cores, not the cgroup quota — so the real width is **32** on this
  box (it was 22 on the Mac). Record the real number, not `/meta`'s.
- **Single sample.** One run of 10 documents says nothing about variance.
