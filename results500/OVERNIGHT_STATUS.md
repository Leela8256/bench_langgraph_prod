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
