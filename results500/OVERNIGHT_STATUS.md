# PDF-500 status log (append-only, PDT)

- **10:55** Pre-flight begun per handoff order. git init + initial commit
  b0d2523 (venvs/datasets excluded). caffeinate verified running.
- **11:00** Containers recreated at 12 CPU / 10 GB (handoff-preregistered;
  changed from snapshot's 12 GB, recorded). Images unchanged: LG ef6b2654,
  RR 55372941. RR /work restaged (corpus 1000, shared pipeline file, driver).
- **11:02** ground_truth_rr.jsonl distilled: 140 clean docs. 500-doc
  manifest written with a fresh selection-rule string (pdf200's misleading
  string not copied).
- **11:03** Probe: LG 10/10 byte-exact GREEN. RR 0/10 — HARNESS BUG (mine):
  rr_pool_state.json restaged empty via docker-exec heredoc without -i;
  driver crashed pre-warmup. Fixed by writing the file with printf; no
  product code touched; rerunning probe.
- (status file created fresh above if missing; heredoc-order artifact)
- **11:07** RR probe rerun after harness fix: GREEN — 10/10, byte-exact,
  8/8 slots warmed, identity verified. (Second apparent failure was a
  swallowed-heredoc shell bug of mine — the rerun had never executed.)
- **11:20-11:47** RR calibration 50@c4 from clean state: 22 ok (p50 2.1 s)
  then WEDGE — 28 consecutive 300 s timeouts across all 8 slots. Clean
  engine, freshly warmed, wedge onset at doc ~23 under c4. All-slot
  simultaneous starvation → server-side pipeline limit, not per-connection.
  HARNESS BUG found+fixed (logged): probe and calibration shared a container
  output dir (parent-based naming); records were separable by meta line;
  naming now includes the leaf dir. Hung driver process killed post-run.
- **11:56** LG calibration 50@c4: VALID — 50/50, span 44.9 s, 1.115 docs/s,
  p50 1.57 s / p99 21.5 s.
- **12:00** Timeouts FROZEN: LG 107.6 s; RR 1500 s (p99 = the 300 s cap —
  circularity recorded in frozen_params.json, applied as written).
  PREDICTIONS_PDF500.md committed. Pre-flight complete; STOPPED for
  operator report before the shot per instruction.
- **12:05** GO received. Arm order: RR first, then LG (fixed choice,
  recorded). INTERPRETATION CALL: wedge detection runs IN-DRIVER (the
  driver knows completions and in-flight precisely; 300 s no-progress);
  the host-side mtime watchdog remains as a 10-min backstop for a dead
  driver. Clean restart + warm before each arm, as in calibration.
- **12:25** RR SHOT COMPLETE: 0/500 completed. Wedge #1 at 0 records on
  attempt 1 (clean engine, successful warmup); relaunch per protocol
  (reap + fresh pool + successful warmup); wedge #2 at 0 records on
  attempt 2; arm stopped. Census reconciled 500=500, all wedge_affected.
  Diagnostics: backend at 4.7 GB RSS / 273 threads at wedge (vs ~0.6 GB
  normal) + defunct jspawnhelper — memory/thread blowup under concurrent
  admission is the freeze mechanism. Span 625 s. P1 was DIRECTIONALLY
  right but optimistic (predicted ≲80 completions; actual 0). P4
  confirmed, onset earlier than predicted bound.
- **12:27** LG shot starting: clean restart, prime, samplers, open-loop
  500 @ frozen 107.6 s timeout.
- **12:40** LG SHOT COMPLETE: protocol census 0/500 (all client timeouts at
  frozen 107.6 s) — but server-side telemetry shows ~61 docs completed
  in-window at 17.94 avg cores; the frozen formula, not the server, set the
  zero. RR/LG zeros have opposite causes; report distinguishes them.
- **12:45** Post-run validation complete: reconciliation ✓, drift ✓,
  samplers gap-free ✓, duplicates none ✓, gates vacuous (0 completions,
  reported as such), predictions scored (P1 directional / P2 refuted-as-
  stated-risk-realized / P3 inconclusive / P4 confirmed-stronger).
  REPORT_PDF500.md written. Final commit follows.
- **15:20** Operator challenge accepted: emulation may CAUSE the wedge, not
  just distort timings (no linux-arm64 build exists; we run linux-x64
  emulated). Attribution caveat added to REPORT_PDF500, RESULTS_SEQ200,
  CONTEXT_SNAPSHOT. Diagnosis experiments running: instrumented c4 PDF
  repro DID NOT wedge this time (44/50 ok — nondeterminism now observed in
  both directions, consistent with an environmental/racy mechanism);
  discriminating pair in flight: txt-c8 (no Tika/JVM) vs pdf-c8 (Tika).
