# Setup and Run — the video benchmark, explained simply

What this folder is, what the moving parts are, and exactly what happens
when a benchmark run executes. No waves: with the 1 TB disk there is
enough room to process the whole corpus in one go, so the run is a
straight line. (The wave machinery still exists in `run/run_waves.sh` for
future corpora too big for the disk — see the note at the end.)

## The cast of characters

There are only four things involved:

1. **The box** — one AWS machine (32 processor cores, 61 GB memory, 1 TB
   disk). Everything runs here. Your laptop only starts/stops it and
   reads results.
2. **S3** — Amazon's file storage. It permanently holds two things: the
   **corpus** (the meeting videos, prepared and ready) and the **results**
   of every run. Think of it as the filing cabinet: the box is
   disposable, S3 is not.
3. **Two "arms"** — the two systems being compared, each packaged as a
   container (a self-contained app the box can start and stop):
   - **RocketRide** — the commercial engine. We install it exactly as
     shipped (version 3.3.1), plus two small documented repairs: one that
     lets it start on Linux at all, and one that stops it from
     accidentally sending every result twice. Both repairs are recorded;
     results always say "3.3.1 with the documented corrections".
   - **LangGraph** — our own service, built to do exactly the same job
     step for step, using the same models and the same settings.
4. **The client** — a third container that feeds videos to whichever arm
   is being tested and writes down what comes back. It is separate on
   purpose: the effort of *sending* videos must never be counted as part
   of the arm's own effort.

## What the pipeline does to one video

Both arms perform the identical five steps on every video:

```
a 30-minute meeting video (~140 MB)
  → take one snapshot frame every 15 seconds        (~120 frames)
  → run an object detector on each frame            ("person", "chair", ...)
  → write the detections as text, one line per frame
  → cut that text into ~4,000-character chunks      (~20-130 per video)
  → turn each chunk into a 384-number "embedding"   (for search)
→ hand back the chunks + embeddings
```

The details that make the two arms comparable were taken from
RocketRide's own real output, so both produce the same *kind* and
*amount* of work. We do not require the outputs to be byte-identical
(the two arms use slightly different builds of the detector) — instead
the correctness checks confirm the work is equivalent (see
METRICS_EXPLAINED.md).

## How a run happens, start to finish

```
laptop:  start the box  →  box pulls the latest code from git
box:     1. get the corpus     S3 → a folder on the box's disk
                               (skipped if already there from last time;
                                ~25 seconds for 30 videos)
         2. build/refresh the containers (cached: usually a minute)
         3. ARM ONE (RocketRide):
              - start the engine container, wait until it answers
              - the client sends 2 practice videos first ("warm-up") —
                these load the AI models into memory and are NOT counted
              - the client then sends the measured videos (for example,
                6 at a time until all 28 are done)
              - meanwhile a small watcher notes, every 15 seconds, how
                much processor and memory the engine is using
              - when done: the engine container is deleted. This matters:
                RocketRide keeps its own copy of every uploaded video and
                only deleting the container gets that space back.
         4. ARM TWO (LangGraph): exactly the same, same videos, same
            order, same 6-at-a-time.
         5. The report runs: correctness checks first, then the numbers.
         6. Everything is uploaded to S3 — in fact it has been uploading
            every 60 seconds all along, so even a crash loses nothing.
laptop:  stop the box.
```

Only one arm ever runs at a time, so each gets the whole machine.

## What lands in S3 for every run

- `per_doc.jsonl` — one line per video: did it succeed, how many frames/
  detections/chunks, fingerprints of every chunk and of the embeddings,
  timings. This is the raw truth; every number in every report can be
  recomputed from it forever, on any machine.
- `progress.jsonl` — a line per video *as it finished* (live progress).
- `engine_cgroup.csv` — the every-15-seconds processor/memory samples.
- `driver.log`, `service.log` — what the client and the arm printed.
- `report.txt` — the checks and numbers, computed at the end.

## Reading the results

```bash
aws s3 cp --recursive s3://rocketride-benchmark-data/leela/videobench/<run>/ ./run --profile leela
python3 bench/report.py ./run/rr                 # one arm
python3 bench/report.py --arms ./run/rr ./run/lg # both, compared
```

The report always prints the correctness checks first. If any check
fails, the numbers are labeled diagnostic-only. A single run also cannot
prove repeatability (that needs the same run done at least twice), so
one-off runs are labeled "sizing evidence", not benchmark results.

## Why there are no waves anymore

Earlier, the box had a 100 GB disk. Because RocketRide keeps a copy of
every uploaded video until its container is deleted, a big run could
fill the disk mid-flight (it happened once). The fix then was "waves":
process a slice, delete the container, reclaim the space, repeat.

The disk is now 1 TB. The whole AMI corpus (24 GB) plus RocketRide's
copies (another 24 GB) uses under 5% of it, so each run simply goes in
one straight pass. The wave runner is kept for the day a corpus larger
than the disk shows up (e.g. a 5,000-video campaign, whose engine copies
alone would near 700 GB) and as a checkpointing tool for very long
campaigns — but for AMI-sized work, nothing needs it.

## The traps worth knowing (all learned the hard way)

- Always start long runs detached (`nohup ... &`) — the box's terminal
  sessions are short-lived, and the box auto-stops when it looks idle.
- The `aws` command is invisible to background scripts unless called by
  its full path — the scripts handle this; keep the pattern.
- RocketRide's disk usage only shrinks when its container is DELETED —
  stopping is not enough.
- Laptop credentials expire every few hours (browser re-login); the
  box's own credentials never do, so running jobs are never affected.
