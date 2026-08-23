# ARCHIVE_FILMS.md — the archive_films corpus: complete data plan

The long-video corpus for the video benchmark: public-domain feature
films from the Internet Archive, staged to S3 through the same pipeline
discipline as the AMI corpus. This is the definitive plan: what the
dataset is, exact sizes and hours, how it gets into S3, every check
along the way, what the 10-film smoke proved, and current status.

Status (2026-08-22): **10-film smoke set staged and benchmarked** (both
arms 8/8, run `archive10-20260822T190711Z`); **full-collection census
in flight**; 500-film staging queued behind the pin.

---

## 1. The dataset

**Internet Archive `feature_films` collection** —
https://archive.org/details/feature_films

| property | value |
|---|---|
| collection size | 28,468 entries (few dozen are subcollections; rest are films) |
| license | public domain / CC — no account, no token, plain HTTP |
| duration profile | **86% ≥ 60 min**, median 82 min, p25 70 / p75 99 (probed sample) |
| eligible 1-hour-plus pool | thousands (exact count lands with the census) |
| what we take per film | the modern **h.264 MP4** derivative (~0.4 GB/h; `MPEG4` fallback) |
| audio | mostly talkies; silent prints filtered twice (census + staging gate) |

### The target corpus (N=500)

| quantity | value | basis |
|---|---|---|
| films | 500 (top-downloads of the eligible pool) | pin from census |
| footage | **≈ 700 hours** (median ~82 min/film) | duration profile |
| bytes in S3 | **≈ 300 GB** | ~0.4 GB/h × derivative sizes (10-film smoke: 5.5 GB for 13.3 h — extrapolates to ~290 GB) |
| S3 cost | **≈ $7/month** | 300 GB × $0.023 |
| staging wall time | **~10–16 h (one overnight)** | archive.org at 5–20 MB/s, sequential |
| box cost to stage | ~$15–25 | c7i.8xlarge $1.43/h |

### Eligibility filters (applied at census, in this order)

1. `mediatype:movies` (drops subcollection entries)
2. not dark (metadata present with files)
3. duration **60–240 min** (1-hour-plus requirement; cap keeps a 10-hour
   outlier from distorting wave sizing)
4. has an `h.264` (preferred) or `MPEG4` `.mp4` derivative with a
   `length` field
5. **not in `silent_films`** (dual-lane needs speech)
6. **deduped by normalized title** (the collection has re-uploads)
7. rank by all-time downloads (popularity ≈ print-quality proxy;
   snapshot recorded), take top N

## 2. What the 10-film smoke proved (run `archive10-20260822T190711Z`)

- **The pipeline works on this data**: `.mp4` routes into RocketRide's
  webhook video lane (previously only proven for `.avi`); both arms
  processed 8/8 films (63–105 min, incl. a 1080p print) with valid
  structure; `corpus_pin` verified all shas.
- **Staging works**: 10/10 downloaded (no retries needed), 0 audio-gate
  skips, probed, sha'd, manifest complete — ~30 min for 5.5 GB.
- **The one real finding — VFR frame divergence**: old prints carry
  variable/odd frame timestamps; RocketRide's reader over-samples them
  by up to +6% vs the video clock while ffmpeg (LangGraph) samples
  exactly. Detections and chunks stayed inside all equivalence bands
  (workload ratio 1.012). **Fixed in the gates** (see §5) and
  **instrumented in the stager** (per-film fps + counted frames in the
  manifest) so VFR-suspicious prints are identifiable.
- Performance notes: long films are RocketRide's best case (41.9× — blast
  amortizes per-doc overhead); LangGraph's decode share doubles (~17%)
  on high-res prints.

## 3. The corpus contract

One corpus doc = one film = `<identifier>.mp4` in
`s3://rocketride-benchmark-data/leela/corpus/archive_films/`.

**The pin is the corpus definition**, committed to git:
- `corpus/sets/archive_films.txt` — `identifier<TAB>filename`, one per film
- `corpus/sets/archive_films_durations.json` — census durations

**The manifest** (built at staging, uploaded beside the videos) carries,
per film:

| field | source | feeds |
|---|---|---|
| `duration_s` | census metadata | the footage denominator (x_realtime, cpu_s_per_footage_min) |
| `video_duration_s` | ffmpeg null-mux probe | the frame_law denominator (A/V and container clocks disagree — the AMI lesson) |
| `sha256` + `bytes` | staging | the corpus_pin gate; content identity forever |
| `frames_counted` + `nominal_fps` | same probe pass | VFR diagnostics (the films lesson) |
| `skipped_no_audio` list | staging audio gate | explains any staged-count shortfall vs the pinned N |

## 4. The staging pipeline (mirror of the AMI v2 discipline)

`run/stage_archive_films.sh`, ON THE BOX (direct archive.org → box → S3;
nothing routes through the laptop):

```
for each (identifier, filename) in the pin:
  skip     if already in S3 AND probed          (resume state: sha journal + S3 listing)
  fetch    S3 copy if staged, else curl from archive.org
           (302 → datanode, retries, ~5–20 MB/s, SEQUENTIAL — politeness)
  gate     audio stream MUST exist (ffprobe, ffmpeg-stderr fallback)
           → silent prints SKIPPED and logged, never staged
  probe    one ffmpeg null-mux pass → video_duration_s + frames_counted + nominal_fps
  record   sha256 + bytes + probe fields → journal
  upload   aws s3 cp → S3
  clean    delete local copy                     (peak disk = ONE film, <4 GB)
then: manifest (durations + shas + fps probe + skip list) → S3
```

Operational hardening (each from a real incident):
- **Keepalive**: a one-core busy loop runs for the stager's lifetime —
  the box idle-watchdog stops network-bound work otherwise (it killed
  the AMI staging mid-run).
- **Resumable at every step**: interruption of any kind costs nothing;
  relaunch continues at the first unstaged film.
- **`aws` resolved by absolute path** (nohup shells lose it).
- Peak disk ~4 GB regardless of corpus size (delete-as-you-go), so
  disk can never be the failure mode.

Launch:

```bash
bash aws_bench/local/box.sh start
bash aws_bench/local/box.sh run 'cd ~/bench_langgraph_prod && git pull --ff-only origin aws-bench'
bash aws_bench/local/box.sh launch stagefilms \
  'cd ~/bench_langgraph_prod/aws_videobench && bash run/stage_archive_films.sh'
bash aws_bench/local/box.sh tail stagefilms       # progress: "staged <id>.mp4 (n/500 ...)"
```

## 5. Checks — before, during, and after

**At census/pin (before any video byte):** eligibility filters (§1),
dark-item skip, dedupe; the script prints filtered-out counts and the
eligible-pool size so the real census is visible before staging.

**At staging (per film):** audio-stream gate; download integrity (curl
`-f` + retries); probe sanity (duration parses, frames counted); sha
recorded before upload.

**After staging (verification):**
```bash
aws s3 ls s3://rocketride-benchmark-data/leela/corpus/archive_films/ --profile leela | grep -c '.mp4$'
# == manifest n_docs (+ skipped_no_audio explains any shortfall vs 500)
aws s3 cp s3://.../archive_films/corpus_manifest.json - --profile leela | python3 -m json.tool | head -30
# spot-check 2–3 shas against fresh downloads; ffprobe 2–3 files (1 video + 1 audio stream)
```

**At benchmark time (the V0 suite, with the films-driven recalibrations
of 2026-08-22 — validated against all three existing run datasets):**

| gate | behavior on this corpus |
|---|---|
| `corpus_pin` | verifies every input sha against the manifest (proved on the smoke) |
| `frame_law` | asymmetric: silent frame DROPS hard-fail; VFR over-sampling ≤10% warns (annotated); dense-content chunk bound at 3×frames+1 |
| `frame_parity` | banded: exact PASS / ≤10% relative WARN ("VFR-band") / beyond FAIL |
| everything else | unchanged: census, structure, self-dup, detection ratio 0.90–1.10, chunk ratio, tight parity, coverage, determinism (needs ≥2 reps) |

## 6. After staging: how it gets benchmarked

- Smoke shape exists: `run/archive10.sh` (RR blast default threads vs LG
  c32) — scale N as needed.
- Full runs at 500: RR blast ≈ 17 h/rep at its measured 41.9× on films;
  LG ≈ 4.7 h/rep at ~150×. Waves (`run_waves.sh`, S3→RAM slices of
  30–40 films ≈ 25 GB) if bounded scratch/checkpoints are wanted; with
  1 TB disk a straight pass also fits (500 films ≈ 300 GB corpus +
  300 GB engine scratch + fixed ≈ ~650 GB peak — inside 1 TB, but waves
  give checkpoints for a 17-hour arm).
- Role in the study: the **long-video corpus** beside AMI's matched
  corpus — AMI carries the strict identical-work claims (frame parity
  exact); films carry realism, `.mp4`, hour-plus documents, and scale,
  reported with the VFR band documented.

## 7. Where everything lives

| thing | location |
|---|---|
| census (full-collection EDA) | `corpus/census_archive_films.jsonl` |
| pin (the corpus definition) | `corpus/sets/archive_films.txt` + `archive_films_durations.json` |
| census/selection script | `corpus/census_archive_films.py` (`--n 500`, `--census-only`, `--select-only`) |
| stager | `run/stage_archive_films.sh` |
| canonical corpus | `s3://rocketride-benchmark-data/leela/corpus/archive_films/` (+ manifest) |
| smoke runner / results | `run/archive10.sh` / `s3://…/videobench/archive10-20260822T190711Z/` |
| resume state (box) | `~/stage_films_shas.jsonl` + the S3 listing |
| box control (laptop) | `aws_bench/local/box.sh` |

## 8. Known traps (all encountered or engineered against)

1. **Dark items** — metadata without files; census flags, selection skips.
2. **Subcollections in search results** — filtered by `mediatype:movies`.
3. **Silent films** — double-gated (census collection filter + staging
   audio probe); skips logged in the manifest.
4. **Re-uploads/duplicates** — title-level dedupe; remakes deliberately kept.
5. **VFR/odd timestamps in old prints** — the measured +≤6% RR
   over-sampling; gates banded, fps probe recorded per film (§5).
6. **Politeness to archive.org** — sequential downloads, retries with
   delay; never fan out.
7. **Idle watchdog vs network-bound staging** — keepalive core (§4);
   nohup-detach always; resume makes any stop free.
8. **`aws` PATH in nohup shells** — absolute path in every script.
9. **Licensing** — PD/CC content, private bucket, internal benchmarking;
   identifiers in the manifest keep provenance traceable.

## 9. Timeline to a benchmark-ready 500-film corpus

| phase | duration | where |
|---|---|---|
| census (in flight) | ~45 min | laptop, metadata only |
| pin top-500 + commit | minutes | laptop |
| staging | ~10–16 h, one overnight | box (keepalive on, resumable) |
| verification | ~10 min | laptop |
| **total** | **~1 day, mostly unattended** | |
