# PDF-200 stepped-concurrency run — status log (append-only, PDT)

- **15:52** Run begins. Corpus = first 200 of the recorded deterministic
  order (results200/manifest.jsonl). Host/emulation facts unchanged from
  PDF-1K provenance (M5 Pro 18c/24GB; x86-on-arm64 — relative only).
- **15:55** MANDATORY LG FIX applied: graph nodes converted to plain sync
  functions (executor dispatch), 18 tests green, image rebuilt, containers
  recreated at 12/12g. INTERPRETATION CALL — serving layer: official
  LangGraph Server is both unavailable here and forbidden by the project's
  standing no-Agent-Server rule; using the existing minimal FastAPI wrapper,
  labeled "library behind minimal FastAPI wrapper" in the report.
- **15:58** OMP_NUM_THREADS=1 pinned on BOTH arms (was already on LG;
  added to RR compose env), recorded.
- **16:05** DEVIATION (transport, logged): host-side RR pool client is
  impossible — the engine rejects WebSocket upgrades arriving through
  Docker's published-port proxy ("did not receive a valid HTTP response";
  works container-internally). Small product finding in itself. RR driver
  therefore runs in-container; its checkpoint file is live-streamed to the
  host via ONE long-lived exec stream, and the watchdog runs host-side on
  checkpoint mtime — never docker-exec polls (PDF-1K lesson).
- **16:10** STAGE 0: three clients calling use() with the same project_id
  + use_existing=True all received the SAME token; engine spawned exactly
  one backend worker on first use (1→2 procs, 27→229 threads; ~202 threads
  ≈ default threadCount 64 instance), none on uses 2–3. Preregistered rule
  → shared instance, POOL SIZE = 8. LG executor width recorded: 22
  (min(32, cpu_count 18 + 4)). Model identity/dim confirmed both arms.
- **16:12** RR ground-truth capture pass running (200 docs, closed-loop
  level 1, unmeasured — per-arm ground truth; RR's parse is engine-internal
  so its expected hashes come from this pre-run capture, logged as the
  standing methodology adaptation).
- **16:20** PREDICTIONS_PDF200.md committed (pre-probe, pre-calibration),
  including the P2-a/P2-b disambiguation the level-64 step exists to test.
- **23:05** INCIDENT (environmental): the host MacBook slept from ~16:35 to
  ~22:38 PDT, suspending the Docker VM and freezing the RR ground-truth
  pass mid-flight. No crash, no data loss — monotonic clocks paused with
  the VM and the driver resumed cleanly on wake (143/200 done, ~9 s/doc
  actual). All wall-clock-adjacent timestamps from this window are suspect;
  the capture pass is unmeasured so no metric is affected. MITIGATION:
  `caffeinate -dims` now runs for the remainder of the benchmark; host
  sleep is impossible for the chain. Logged because a silent 6-hour pause
  would otherwise look like a wedge in any postmortem.
- **23:25** SCOPE CHANGE (user-directed): single pass, no repetitions.
  Calibration stage dropped; per-doc timeout provisional 300 s both arms
  (recorded). The running ground-truth capture doubles as RR level 1
  (closed-loop, timed, same protocol). Remaining: probe gate, then one
  pass — RR L4/16/64, LG L1/4/16/64 — then report. Repeatability/variance
  claims are out of scope for this report and will be labeled accordingly.
- **00:15 (Aug 9)** RUN STOPPED BY DECISION: from ~000747 onward every doc
  was a consecutive 300 s timeout — the shared pipeline wedged (Phase-R
  signature; poison-doc region around 000744) and this capture driver has
  no relaunch-recovery by design. 174/200 records: 140 ok, 3 genuine
  failures (2 no-documents, 1 isolated timeout), ~31 wedge-victim timeouts
  that are NOT per-doc results. Remaining 26 docs would have burned 300 s
  each for nothing. Data pulled; results assembled from the 140+3.
- **06:20 (Aug 10)** CONTEXT_SNAPSHOT.md written: live state, artifact map,
  findings not previously in any file, dead ends, and all open decisions.
  Correction recorded there and in RESULTS_SEQ200.md: 000163.pdf (the Aug 7
  wedge doc) succeeded in this run — wedging is state-dependent, not a
  per-document property. Stale chain monitor stopped; no processes remain.
