# AWS run — checklist

Goal: run the entire benchmark on the native x86_64 box, producing the first
timings where `engine_native: true` / `equivalent_execution_conditions: true`.
Every check drops its evidence artifact into this folder (small text/JSON only —
raw run data stays on the box and goes to S3, never into git).

**Box:** i-0bdc8b1e18f2a5348 — c7i.8xlarge, 32 vCPU / 61 GB, 100 GB gp3,
us-east-1a, x86_64. Login: `aws sso login --profile benchmark`.
**Access model:** interactive SSM shell only (`aws ssm start-session --target
i-0bdc8b1e18f2a5348`). No scp, no SendCommand, no port forwarding. Code gets on
via `git clone` of the public repo; data via S3 or re-download; results leave
ONLY via `aws s3 cp` from the box (instance role).
**Gate:** terraform PR #204 (BenchmarkBoxOperator). Until merged+applied, SSO
login shows "No AWS accounts are available to you". Dmitrii pings on merge.

> ⚠ **Auto-stop trap:** the box stops itself after 1h below 20% CPU with NO
> warning. Disk survives; restart with
> `aws ec2 start-instances --instance-ids i-0bdc8b1e18f2a5348`.
> Long low-CPU steps (image pulls, dataset downloads, report writing) can trip
> it — keep an eye on the session. NEVER run a keep-busy loop during a measured
> run; it contaminates cpu_s.

---

## Phase 0 — Local prep (do NOW, no AWS access needed)

- [ ] **0.1 Repo state clean & pushed.** Commit the pending changes (gate50/,
      metrics/) to `main`; refresh the curated public mirror
      (`public-main` → github.com/Leela8256/bench_langgraph_prod) since that
      repo is the ONLY way code reaches the box. Verify the public repo
      contains everything a run needs: `metrics/`, `gate50/`, `pdf200/`,
      `pdf500/`, `pdf1k/`, `diagnosis/wedge_capture.sh`, `docker-compose.yml`,
      `langgraph-fastapi/`, `rocketride/`.
      → artifact: `00_repo_state.md` (local + public HEAD SHAs, verification notes)

- [ ] **0.2 Dataset transfer plan + integrity manifest.** `datasets/` is 639 MB
      (govdocs, txt100, fault30 + fault30_manifest.json) and is excluded from
      the public repo. Generate `sha256sum` over every dataset file now; the
      box verifies against it whichever transfer path wins:
      (a) local → S3 → box, if BenchmarkBoxOperator can PutObject (test in 1.5), or
      (b) re-download govdocs on the box from source + reconstruct txt100/fault30.
      → artifact: `01_datasets.sha256` + `01_dataset_plan.md`

- [ ] **0.3 Box preflight script.** `preflight.sh` (in this folder): arch,
      cores, RAM, disk, docker/compose versions, git, python3, network
      reachability. First thing run on the box; output pasted back.
      → artifact: `preflight.sh` (now) — output recorded in Phase 1

- [ ] **0.4 Run plan with core-dependent config recomputed.** 32 vCPU changes
      things vs the Mac: LG executor width = min(32, cores+4) = **32**;
      confirm RR threadCount; expected durations per phase; the exact command
      sequence for Phase 3. Document every config value that differs from the
      Mac runs so no number is silently compared across hosts.
      → artifact: `02_run_plan.md`

- [ ] **0.5 S3 exfil convention.** Key scheme (proposal:
      `s3://<bucket>/prod-bench/<utc-date>_<phase>/…`), and the rule: upload
      RAW per-doc JSONL records + sampler streams, not just reports — metrics
      are derived post-hoc, so raw records are the thing that must survive.
      Bucket name is discovered in 1.5.
      → artifact: `03_s3_exfil.md`

## Phase 1 — Access verification (when PR #204 merges)

- [ ] **1.1 SSO works:** `aws sso login --profile benchmark` succeeds and shows
      account 250017478984 / role BenchmarkBoxOperator.
- [ ] **1.2 Instance starts:** `aws ec2 start-instances --instance-ids
      i-0bdc8b1e18f2a5348` → state `running`.
- [ ] **1.3 Shell lands:** `aws ssm start-session --target i-0bdc8b1e18f2a5348`
      → prompt as `ssm-user`; docker works without sudo.
- [ ] **1.4 Preflight passes:** run `preflight.sh` on the box; expect x86_64,
      32 cores, ≥90 GB free disk.
      → artifact: `10_preflight_output.txt`
- [ ] **1.5 S3 path discovered:** on the box, `aws sts get-caller-identity` +
      `aws s3 ls` to find the bench bucket; round-trip test (box writes a file
      up, local profile tries to read it down). Result decides dataset path
      (a) vs (b) from 0.2.
      → artifact: `11_s3_access.md` (bucket name, both directions tested)

## Phase 2 — Box provisioning

- [ ] **2.1 Clone:** public repo cloned on box; HEAD SHA matches 0.1.
- [ ] **2.2 Datasets on box, verified:** transfer per 0.2 decision;
      `sha256sum -c 01_datasets.sha256` → zero mismatches (hard fail on any).
      → artifact: `20_dataset_verify.txt`
- [ ] **2.3 Images built/pulled NATIVE:** RR engine image runs linux/amd64
      natively for the first time. Record image digests + `docker version`.
      → artifact: `21_images.md`
- [ ] **2.4 Stack boots:** both arms + tika sidecar healthy; health endpoints
      answer.
- [ ] **2.5 Smoke run:** gate50 `smoke.pipe` / small-N pass on both arms;
      export shows `engine_native: true` and cross-arm chunk hashes still
      byte-identical (the Tika-matched property must survive the platform move).
      → artifact: `22_smoke.md`

## Phase 3 — The runs (in this order)

- [ ] **3.1 WEDGE ATTRIBUTION FIRST:** `diagnosis/wedge_capture.sh`,
      pdf-c8×100, ×3 reps. This closes the open product-vs-emulation question
      (Tika parse-stage wedge: 167/205 threads in rt_mutex_schedule under
      emulation). Exfil captures to S3 IMMEDIATELY after each rep — this
      evidence is the single most valuable output of the whole AWS exercise.
      → artifact: `30_wedge_verdict.md` (wedges natively? yes/no + capture S3 keys)
- [ ] **3.2 Gate-50:** both arms, blast + sequential; all M0 gates green;
      `metrics/selfcheck.py` reproduces the report from raw records.
      → artifact: `31_gate50.md`
- [ ] **3.3 pdf200** stepped closed-loop (`pdf200/chain200.sh` protocol).
- [ ] **3.4 pdf500.**
- [ ] **3.5 pdf1k** (`pdf1k/overnight_chain.sh`) — ⚠ check the chain has no
      >1h low-CPU gap between reps, or the auto-stop kills the overnight run.
- [ ] **3.6 Fault run:** fault30 manifest → M4 blast radius + M5 isolation.
- [ ] **3.7 Metrics + LOC:** full M0–M7 derivation from raw records on the box;
      `python3 -m metrics.m6_loc`; selfcheck green.
      → artifacts: `32_pdf200.md` … `36_metrics.md` (per phase: S3 keys of raw
      records, gate verdicts, headline numbers)

## Phase 4 — Exfil, verify, teardown

- [ ] **4.1 Everything to S3:** raw JSONL records, sampler streams, exports,
      wedge captures. `aws s3 ls --recursive` listing captured.
- [ ] **4.2 Local recompute proves exfil complete:** download raw records
      locally, run `metrics/selfcheck.py` against them — same numbers as
      on-box. If it recomputes, nothing essential was left on the box.
      → artifact: `40_exfil_verified.md`
- [ ] **4.3 Stop the instance:** `aws ec2 stop-instances --instance-ids
      i-0bdc8b1e18f2a5348`.
- [ ] **4.4 Writeups updated:** ISSUE_RR_ENGINE_WEDGE.md amended with the
      native verdict; CONTEXT_SNAPSHOT.md updated; memory updated.
