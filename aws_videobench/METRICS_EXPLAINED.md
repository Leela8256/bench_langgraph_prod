# Metrics and Correctness, explained simply

The plain-language companion to `METRICS.md` (the terse spec) and to the
code in `bench/metrics/`. Everything here is what the report actually
prints, in the order it prints it, and why each item exists.

One rule governs everything: **checks come first, numbers second.** If a
correctness check fails, the numbers still print — but stamped as
diagnostic-only, never quotable. And if something couldn't be measured,
it shows as "None", never as zero: a broken run must never look like a
fast one.

---

## Part 1 — The correctness checks ("did the run actually do the work?")

Run per arm, before any number is trusted:

**1. Census — did every video come back?**
We sent 28 videos; we must get exactly 28 answers, each one matched to
its video by name, none duplicated, none missing. A video that returns
an empty answer counts as a failure, not as a fast success. Any failure
is listed by name — nothing disappears silently.

**2. Structure — is each answer well-formed?**
Every video must produce at least one chunk; every embedding must have
exactly 384 numbers, all of them real (no infinities), each scaled to
length 1.0 as the model promises; the chunk-count bookkeeping must be
internally consistent; no chunk may exceed the splitter's size.
This catches a wrong model, a broken load, or truncated output.

**3. Frame law — did it really watch the whole video?**
A 30-minute video sampled every 15 seconds must yield about 121 frames —
simple arithmetic. If an arm reports meaningfully fewer, it silently
skipped part of the video. We allow ±1 frame for rounding. There's also
an upper sanity bound on chunks relative to frames, so runaway output
gets caught too.

**4. Self-duplication — did anything get counted twice?**
No two chunks within a video may be identical. This exists because
RocketRide 3.3.1 shipped a real bug that returned every result twice
(inflating its "work done" by ~40% before we patched it). The check
stays forever so a regression would be caught immediately, without
needing the other arm to compare against.

**5. Corpus pin — were these the right videos?**
Each video's fingerprint (a SHA-256, like a tamper-proof serial number)
is compared to the corpus manifest. If a video changed since the corpus
was built, the run fails — results from a drifted corpus are worthless.

**6. Determinism — does the same input give the same output?**
The same video, run twice, must produce byte-for-byte identical chunks —
and now also identical embedding fingerprints. A system that gives
different answers each time can't be benchmarked. A single run can't
prove this (there's nothing to compare against), so single runs
automatically fail this check and get labeled "sizing evidence".

**7. Metric coverage — did we measure everything we claim to?**
Every metric in the report must have a value, or a written reason why
not (e.g. "thread counts don't apply in sequential mode"). This makes it
impossible for "we didn't check" to quietly pass as "it was fine".

**And across the two arms:**
- **Input identity** — both arms ate byte-identical files.
- **Frame parity** — both extracted exactly the same number of frames.
- **Detection ratio** — detections within ±10% of each other (the arms
  run slightly different detector builds, so tiny drift is expected and
  flagged, not failed).
- **Chunk ratio** — chunk counts per video within sane bounds (a hard
  fail outside 0.4–2.5×: that would mean one arm did wholesale different
  work). A stricter version (±1 chunk per video) is also reported as an
  advisory.
- **Workload ratio** — one number: total work of arm A ÷ arm B. 1.0
  means perfectly equal work.

## Part 2 — The numbers ("how fast, how expensive?")

**Speed (V1).** The headline is **× realtime**: how many hours of video
get processed per hour of clock time. 36× means one hour of footage
takes 100 seconds. Alongside it: videos/second, chunks/second,
frames/second (chunks per video vary 10× by room, so we always show the
work-based rates next to the video count), and "sustainable live
streams" — how many live camera feeds this speed is equivalent to
keeping up with.

**Response time (V2).** Reported strictly by how the videos were sent,
because the meanings differ:
- *One or a few at a time*: true per-video response times — the median,
  the 95th percentile ("all but the slowest 5%"), and the 99th. Failures
  are counted separately, never averaged in.
- *Everything at once (batch)*: only the total batch time is exact. We
  also show when the first result appeared and how completions spread
  out — but we refuse to call those "response times", because a video
  that sat in the queue for 40 minutes didn't take 40 minutes to process.
  Every "time to first result" carries a note saying exactly what it
  measured, because different systems' "first result" are not the same
  thing.

**Efficiency (V3) — the fairest comparison numbers.** How much processor
effort per unit of work: CPU-seconds per minute of footage (the primary
one), per video, per frame, per detection, per chunk. Plus:
- **effective cores** — of the 32 available, how many were actually busy
  on average (measured from the operating system's own counters);
- **threads activated** — how many threads the system *created*. The gap
  between these two is a diagnostic in itself: RocketRide created ~1,000
  threads and kept ~5 cores busy — lots of workers, little work.

**Memory & operations (V4).** Peak memory — now the *honest* version
(the raw counter includes the operating system's file cache, which
inflated it 5×; we record the corrected number too). Also: startup time
until ready (excluded from all measurements, reported as an ops cost),
and — for LangGraph only — a breakdown of where time goes (decoding vs
detecting vs embedding). RocketRide can't be broken down that way (it's
a black box inside), and that asymmetry is itself reported.

**Cost (V5).** Dollars per 1,000 hours of footage, straight from the
machine's hourly price divided by the speed. And videos per day one box
can handle.

**Comparing sending styles.** When the same arm is run both one-at-a-time
and concurrently, we compute the speedup and "parallel efficiency" —
how close it got to perfect scaling (6 at a time should ideally be 6×
faster; the shortfall is the interesting part).

## Where this suite came from

Built for the PDF benchmark, extended for video, then cross-checked
against the sibling Haystack benchmark's implemented suite
(`VIDEO-METRICS-IMPLEMENTED.md`) — everything useful from theirs was
adopted on 2026-08-21 (deterministic percentiles, thread accounting,
coverage gate, corpus pin, basis notes, and more). What neither suite
measures yet is also written down (network bytes per video, memory
growth vs video length, crash-recovery behavior), so nobody mistakes
the suite for covering them.

## Where the code lives

- `bench/metrics/v0_gates.py` — every correctness check above.
- `bench/metrics/v_metrics.py` — every number above.
- `bench/report.py` — runs checks, then numbers, prints the verdict,
  and exits with an error code if any hard check failed.

All three work on the saved records alone — download any run from S3 and
recompute everything on a laptop, years later, and get the same answers.
