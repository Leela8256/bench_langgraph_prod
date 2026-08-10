# Diagnosis: why RocketRide stops processing documents

2026-08-10 · evidence: `runs/diagnosis/`, `runs/pdf500/rr/shot_dir/`,
five prior wedge observations across three days · environment: linux-x64
engine under x86-on-ARM emulation (no linux-arm64 build exists)

## Conclusion

**The freeze localizes to the `parse` stage — Apache Tika 3.2.3 running on
a Java 17 JVM embedded inside the engine's backend process.** Under
concurrent PDF parsing, backend threads pile up on a kernel
priority-inheritance mutex and never recover; the JVM's process-spawn
helper (`jspawnhelper`) is repeatedly found frozen mid-handshake. Every
other stage is exonerated by direct experiment.

Whether the lock pileup is a RocketRide/Tika-integration defect or an
emulation pathology (Rosetta translating contended PI-futexes in a
200+-thread process) **cannot be decided on this hardware** — the operator
identified this correctly. A native Linux x64 rerun of the experiment
below settles it in ~15 minutes.

## The discriminating experiment (today, same clean-restart procedure each)

| run | corpus | stage under test | result |
|---|---|---|---|
| pdf-c8 × 100 | PDFs | parse (Tika/JVM) + split + embed | **WEDGED at 18 docs** |
| txt-c8 × 100 | extracted text of the SAME documents | split + embed only (text MIME bypasses parse) | **100/100 clean, 36 s, p50 1.64 s, zero failures** |

Same engine, same 8-connection pool, same offered concurrency, same
downstream text volumes. The only difference is whether the JVM parse path
runs. Splitter and embedder handle sustained c8 perfectly.

## Forensics at the freeze (pdf-c8, captured live)

- Backend process (hosts JVM + Python + torch in one address space):
  **167 of 205 threads blocked in `rt_mutex_schedule`** (kernel PI-mutex
  wait), 38 more in `futex_wait_queue`, 3.0 GB RSS and climbing.
- **`jspawnhelper` (pid 624) alive but stuck mid-handshake**, plus one
  zombie — OpenJDK's fork/exec helper wedged is a known JVM deadlock
  signature, and `fork()` from a heavily-threaded translated process is
  also Rosetta's most fragile path. Seen at every captured wedge.
- Livelock, not idle hang: at the PDF-500 wedge the frozen engine burned
  ~18 cores for 680 s producing nothing.
- No error ever surfaces to the client; WebSocket sessions stay healthy.

## Why every earlier observation now fits

| observation | explanation |
|---|---|
| Warmups always succeed | single doc → no concurrent parse → race never triggers |
| Wedge onset nondeterministic (18, 23, ~175 docs; instant at 500 offered) | race probability scales with concurrent parse operations |
| Same run config wedges one day, completes the next | racy trigger, not a poison document (already corrected in CONTEXT_SNAPSHOT §4.1) |
| Sequential (c1) run wedged at doc 175 | Tika still parses per doc; JVM background threads + spawn path still active — lower probability, nonzero |
| LangGraph never wedges | no JVM anywhere in that arm |
| RR_BENCH ran this engine for weeks without wedging | its pipelines were chat/LLM-shaped — `parse` was never exercised |
| RSS blowup (2.6→8.3 GB observed) | queued/parked parse jobs accumulate behind the stuck lock — symptom, not cause |
| `terminate()` doesn't clear it | the stuck threads never service the teardown; only SIGKILL of the backend clears state |

## What remains open, and the 15-minute test that closes it

Run `diagnosis/wedge_capture.sh` (pdf-c8 × 100, three repetitions) on a
**native Linux x64 host**:
- wedges there too → RocketRide product bug (Tika/JVM integration deadlock
  under concurrent parse) → file with vendor, with these forensics.
- clean there (and clean × several reps) → emulation artifact → strike the
  wedge findings from product conclusions entirely; all capacity census
  results must be re-measured natively regardless.

Also worth running natively: `000164.pdf` / `000357.pdf` single-doc
(deterministic silent-empty responses — likely a genuine parse defect
independent of the wedge, since they reproduce at c1 with clean responses).

## Scope note

All of this binds to engine v3.2.1, default configuration, this container
image. Nothing here measures performance; it explains failure mechanics.
