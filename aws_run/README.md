# aws_run/ — operating folder for the AWS EC2 benchmark run

This folder is the single operating surface for running the benchmark on the
AWS box. It ships inside the public repo (the box can ONLY get code by cloning
that repo), and it separates cleanly into two sides:

```
aws_run/
  README.md            this file — the operating manual
  CHECKLIST.md         master checklist: every step, its script, its evidence
  COMMS.md             how Mac ↔ box communication works (SSM/S3/git, and limits)
  box.sh               LOCAL  — the only command you run on the Mac
  local.env.example    LOCAL  — copy to local.env, fill in AWS identifiers
  local.env            LOCAL  — git-ignored; instance ID / profile / bucket live here
  local/
    make_manifest.sh   LOCAL  — hash datasets/ → MANIFEST.sha256
  MANIFEST.sha256      dataset integrity manifest (generated, committed)
  preflight.sh         BOX    — first command on the box; read-only sanity check
  box/
    20_datasets.sh     BOX    — obtain datasets + byte-verify against manifest
    30_build.sh        BOX    — build/pull images NATIVE amd64, record digests
    40_boot_smoke.sh   BOX    — boot the stack, wait healthy
    50_wedge.sh        BOX    — wedge attribution ×3 reps + immediate S3 exfil
    90_exfil.sh        BOX    — push all raw results to S3, verified listing
  evidence/            filled in as checks pass (small text/JSON only)
```

Box scripts for gate-50 / pdf200 / pdf1k are added once checklist items 0.6
(portability pass) and 0.7 (gate-50 driver decision) are closed — a wrapper
that guesses its entry point is worse than no wrapper.

## Rules (what makes this foolproof)

1. **No credentials in committed files, ever.** `local.env` is git-ignored and
   is where anything environment-specific goes. Instance IDs and bucket names
   DO appear in these docs: this repo is public, and they are inert without
   IAM permission — but keys, tokens and account-level secrets never do.
2. **Code is never edited on the box.** Fix here → commit → push → `git pull`
   on the box. The clone is disposable; S3 holds the results.
3. **Every checklist item leaves evidence** in `evidence/` (small text only —
   raw run data goes to S3, never into git).
4. **Box scripts fail fast and loud.** Each `box/NN_*.sh` refuses to run if
   its precondition isn't met (wrong arch, missing manifest, Mac paths still
   hardcoded) and names the checklist item that fixes it.
5. **Long runs never live inside an SSM session.** `./box.sh launch <name>
   'bash aws_run/box/NN_x.sh'`, then `./box.sh tail <name>`.

## Quickstart, day one (access just landed)

```
cp aws_run/local.env.example aws_run/local.env   # fill in instance ID
./aws_run/box.sh login                           # browser SSO
./aws_run/box.sh start                           # boots box, waits for SSM
./aws_run/box.sh run 'whoami && uname -m && nproc'
./aws_run/box.sh run 'git clone https://github.com/Leela8256/bench_langgraph_prod.git'
./aws_run/box.sh run 'cd bench_langgraph_prod && bash aws_run/preflight.sh'
```

Then follow CHECKLIST.md top to bottom. Full comms details: COMMS.md.
