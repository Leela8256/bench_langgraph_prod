# CONTEXT_SNAPSHOT — 2026-08-10 06:20 PDT

Written at the quiet point after the sequential 200-doc RocketRide run was
stopped. Purpose: everything a person (or a fresh session) needs to pick this
up cold, including the things that only ever existed in conversation.

Read order for someone new: this file → `results200/RESULTS_SEQ200.md` (the
latest results) → `langgraph-fastapi/toil.md` + `rocketride/toil.md` (build
logs) → the `HANDOFF-*.md` files (specs).

---

## 1. Live state right now

| | state |
|---|---|
| `prodbench-langgraph` | up 38 h, healthy, image `ef6b2654…`, 12 CPU / 12 GB |
| `prodbench-rocketride` | up 19 h, healthy, image `55372941…`, 12 CPU / 12 GB (restarted after the wedge — clean) |
| benchmark processes | **none running**, host or container. No leftover `node.py` backends. |
| `caffeinate` | **released** — the host can sleep again. It ate 6 hours once (see §5). Re-arm before any long unattended run. |
| git | **still no repository.** Six days of work exists only as loose files on the Desktop. |

Nothing is in flight. Nothing is scheduled. Stopping here costs nothing.

---

## 2. What changed since the 200-run stop

1. Killed the wedged RR driver and the whole downstream chain (`single_pass.sh`
   and its monitors) — no probe, no concurrency levels, no LG pass ran.
2. Pulled the partial data out of the container →
   `runs/pdf200/gt-rr/per_doc.jsonl` (175 records).
3. Wrote **`results200/RESULTS_SEQ200.md`** — the current results document.
4. Restarted the `rocketride` container to clear the wedge; released
   `caffeinate`.
5. Appended the stop decision to `results200/OVERNIGHT_STATUS.md`.
6. (This file.)

No code changed. No container config changed. No data was deleted.

---

## 3. Where the data is

| path | what |
|---|---|
| `datasets/govdocs/` | 1000 Govdocs1 PDFs, 618 MB, gitignored |
| `results/manifest.jsonl` | 1000-doc manifest (name, bytes, sha256, rule) |
| `results200/manifest.jsonl` | first 200 of the above ⚠️ see §6 wart |
| `results/ground_truth/` | offline pypdf+splitter reference, 999/1000 docs |
| `results200/ground_truth_lg.jsonl` | LG ground truth, 200/200 |
| `runs/pdf200/gt-rr/per_doc.jsonl` | **the RR 200-doc run** (175 records) |
| `runs/prior-parity-run/` | Aug 7 archive: LG 200/200 + the earlier RR attempts |
| `runs/pdf1k/` | the 1000-doc burst run (mostly invalid reps, kept) |
| `results200/RESULTS_SEQ200.md` | current results |
| `PREDICTIONS_PDF1K.md`, `PREDICTIONS_PDF200.md` | pre-registered, uncontaminated |

**Planned-but-never-written:** `results200/ground_truth_rr.jsonl` (the RR data
exists in `gt-rr/per_doc.jsonl`, it was just never distilled — one short
script), `results200/REPORT_PDF200.md`, `runs/pdf200/rep1-{rr,lg}/`.

---

## 4. Findings not yet written down anywhere


> **ATTRIBUTION CAVEAT (added 2026-08-10, after operator challenge):** every
> wedge observation in this project occurred under x86-on-ARM emulation.
> The stuck-jspawnhelper + livelock signature is consistent BOTH with a
> product defect AND with known emulation pathologies (fork/exec of a
> ~300-thread translated process; lock-contention degradation under
> translation). Wedge findings are therefore "reproducible in this
> environment," NOT attributed to the product, until reproduced on a native
> Linux x64 host. RocketRide ships no linux-arm64 build, so no fair native
> containerized comparison exists on Apple Silicon. The deterministic
> per-doc empty-result failures (000164/000357) are likely attribution-safe
> (clean responses, not stress behavior) but also deserve native retest.

### 4.1 The wedge is NOT a poison-document property — correction to my earlier claim

I previously described `000163.pdf` as a poison document because the Aug 7 run
wedged on it. **Tonight `000163.pdf` processed fine.** The wedge happened at a
completely different document (`000744.pdf`).

So the accurate statement is: *some* documents can wedge the shared pipeline,
but which one does it is **not deterministic** — it depends on engine state
and load history, not solely on the document. That is a worse finding than a
poison doc (you cannot blocklist your way out of it) and it invalidates the
"identify the poison doc" approach I was implicitly taking.

### 4.2 Two documents DO fail deterministically

`000164.pdf` and `000357.pdf` both fail with *"no documents returned"* — the
engine completes the call and hands back an empty result with no error.
`000164.pdf` produced the identical failure in the Aug 7 run on a different
engine session. These are reproducible per-doc defects, distinct from the
wedge, and they fail *silently* (success-shaped response, empty payload).

### 4.3 Exact wedge signature (from tonight's data)

From `000744.pdf` onward: 33 records, sequence `T N T×31` — one timeout, one
"no documents", then 31 consecutive 300 s timeouts, **zero recoveries**. Once
wedged, the pipe never came back on its own. The WebSocket stayed healthy
throughout; nothing was surfaced to the client.

### 4.4 The Aug 7 LangGraph image no longer exists

The LG numbers in `RESULTS_SEQ200.md` came from the **pre-fix async-node
image on 2 CPU / 4 GB**. That image was overwritten on Aug 8 by the sync-node
rebuild (`ef6b2654…`). Consequence: **the archive's LG timings can never be
reproduced exactly.** Any LG rerun measures the new (faster, executor-
dispatched) code. Do not treat the archive numbers as a current baseline.

### 4.5 Extractor volume parity is closer than Phase R suggested

Phase R measured RocketRide's parser duplicating ~4.7% of lines — but that was
on a synthetic generated fixture. On 140 real Govdocs PDFs the median RR/LG
character ratio is **0.994** (p10 0.971, p90 1.030). The duplication defect is
real but its aggregate effect on real documents is near zero. Anywhere the
"~3.3% inflation" figure appears in older docs, it over-generalizes from one
synthetic file.

### 4.6 RocketRide's engine rejects WebSocket upgrades through Docker's port proxy

`ws://localhost:5565` from the host fails with *"did not receive a valid HTTP
response"*, while the identical call works container-internally. This is why
every RR driver runs inside the container. It is also a small product finding
in its own right (the published port is effectively unusable for the SDK).

### 4.7 `send_files` has no client-side concurrency cap

Read from SDK source (`rocketride/mixins/data.py`): it builds one coroutine
per file and `asyncio.gather`s them all, commented *"let server handle
queuing."* So the client cannot protect the server, and the ~4-slot admission
ceiling observed under burst is server-side, not a client artifact.

### 4.8 `terminate()` does not reap a wedged backend

Observed a backend surviving at **2.6 GB RSS** (≈4× normal) after its pipe was
terminated, starving later pipes under the container memory cap. Drivers now
`pkill -9 -f 'ai/node.py'` on relaunch. `--autoterm` did not fire.

### 4.9 Stage 0 numbers (measured, in `stage0_decision.json` but worth stating)

Three clients calling `use()` with the same `project_id` + `use_existing=True`
all received the **same token**; the engine spawned exactly one backend on the
first `use` (1→2 processes, 27→229 threads ≈ threadCount 64) and **none** on
uses 2 and 3. Hence pool size 8 against one shared instance. LangGraph's
counterpart default executor width is **22** (`min(32, cpu_count 18 + 4)`).

### 4.10 Embedder truncates at 512 tokens

Chunks are ~4000 chars but the model only sees the first 512 tokens
(saturation measured at ~3000–3500 chars). Affects both arms identically, so
it does not bias comparison — but it means **cross-arm vector similarity is
weak evidence**; chunk hashes are the real gate. In `langgraph-fastapi/toil.md`.

---

## 5. Environmental gotcha that cost real time

The MacBook slept from ~16:35 to ~22:38 on Aug 8, suspending the Docker VM
mid-run. No crash and no data loss (monotonic clocks paused with the VM), but
it looked exactly like a wedge for hours. **Any unattended run needs
`caffeinate -dims` running first.** It is currently NOT running.

---

## 6. Data-quality warts

- `results200/manifest.jsonl` carries `selection_rule: "…first 1000
  survivors"` because it was sliced from the 1000-doc manifest after that
  string was updated. The slice itself is correct (first 200 in deterministic
  order); only the embedded rule text is misleading.
- `runs/pdf200/gt-rr/per_doc.jsonl` has **175 records for a 200-doc corpus**
  and no `level_meta` line (the run was killed before it wrote one). 140 ok,
  2 genuine failures, 33 wedge-affected.
- The PDF-1K run left several zero-record invalid reps (`cal-rr`,
  `rep1-lg`). Kept deliberately per the never-delete rule; they are evidence
  of the failure, not usable measurements.

---

## 7. Dead ends — don't re-tread

| thing | why it's dead |
|---|---|
| `.rrclient/` host venv | Host-side RR client can't connect (§4.6). Kept only as proof it was tried. |
| `pdf200/stage0.py` | Host-side Stage 0; never ran (same reason). Superseded by `pdf200/stage0_incontainer.py`. |
| `pdf200/rr_stepped.py` paths | Rewritten to container-absolute (`/work/...`). It runs **only** inside the container. |
| Watchdogs that poll via `docker exec` | Crawl to a halt against a wedged container — a 10-min watchdog took 2.5 h to fire. Watch checkpoint file **mtime** host-side instead. |

---

## 8. Built and ready, never run to completion

All staged, all tested to the point they were reached:

- `pdf200/single_pass.sh` — probe → RR L4/16/64 → LG L1/4/16/64 → drift
- `pdf200/chain200.sh` — the full 3-rep alternating protocol
- `pdf200/pre_chain.sh` — gt → probe → calibration → freeze → hands to chain
- `pdf200/run_level200.sh` — one (arm, level) with host-side mtime watchdog
- `pdf200/validate200.py`, `pdf200/lg_stepped.py`, `pdf1k/make_report.py`

Restarting the stepped concurrency work is a one-command job:
`bash pdf200/single_pass.sh` (arm `caffeinate` first).

---

## 9. Open decisions — all waiting on you

**Benchmark scope**
1. **Matched-conditions LG rerun** (~40 min) to make the timing comparison in
   `RESULTS_SEQ200.md` fair. Offered twice, never answered. Without it the
   cross-arm latency/throughput numbers stay labeled "indicative only."
2. **Stepped concurrency (levels 4/16/64)** — cancelled mid-flight. The
   P2-a/P2-b question (does an 8-connection pool break RocketRide's ~4-slot
   admission ceiling?) is still unanswered and is the most interesting open
   measurement.
3. **Does `prod bench` supersede or complement `~/Desktop/RR_BENCH`?** Asked
   days ago, never answered. RR_BENCH already has published results with a
   different LangGraph arm (subprocess, not a server).

**Methodology**
4. **`chunk_size` pinned inequality** — logged for you + Shashi to decide.
5. **512-token truncation** — belongs in the methodology notes of any report
   that cites vector parity.
6. **OPEN-1 canonical encoder flags** — the handoff says verify against an
   offline "mt10k reference"; **no such artifact exists in RR_BENCH**. The
   only encoder there uses `sort_keys=True, ensure_ascii=False` (ours:
   `False`/`True`) and is used for logging digests, not response bytes. Do not
   flip our constants without deciding which artifact is authoritative.
7. **OPEN-2 cross-arm schema ratification** with Shashi/Ansh — probably dead
   (the newer handoff dropped LlamaIndex), but unconfirmed.
8. **M2 vs "Phase L" numbering** — two handoffs describe the same document-
   pipeline work under different names; never reconciled.
9. **Phase R deliverable location** — I put them in `prod bench/rocketride/`;
   the handoff said "under `rocketride/` in the working tree" while living
   inside `langgraph-fastapi/`. Easy to move.

**Engineering hygiene**
10. **`git init`** — offered twice, never answered. Nothing is version
    controlled; there is no diff and no backup.
11. **File the RocketRide defects?** The wedge (§4.1/4.3), the silent empty
    results (§4.2), the un-reaped 2.6 GB backend (§4.8), and the port-proxy WS
    rejection (§4.6) are all reproducible product bugs. You work at
    RocketRide; I have not filed or reported anything anywhere.

---

## 10. If you want the fastest path to a defensible report

1. `caffeinate -dims &`
2. Rerun LG on current containers, 200 docs sequential (~40 min) → matched
   timing pair.
3. Distill `runs/pdf200/gt-rr/per_doc.jsonl` → `results200/ground_truth_rr.jsonl`.
4. Regenerate the results doc with both arms on equal footing.

That yields one honest, fully-labeled sequential comparison. The concurrency
curve is a separate, larger commitment (~2 h for a single pass).
