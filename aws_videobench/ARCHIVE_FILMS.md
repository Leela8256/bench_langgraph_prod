# ARCHIVE_FILMS.md — the archive_films corpus: what it is and how to build it

The long-video corpus for the RocketRide-vs-LangGraph video benchmark:
public-domain feature films from the Internet Archive, staged to S3
through the same pipeline as the AMI corpus. Written so a teammate with
AWS access and this repo can build the whole corpus without any other
context. Read alongside `DATA_FLOW_PLAN.md` (the AMI original this
mirrors) and `LONG_VIDEO_SOURCES.md` (why this source was picked).

---

## 1. The dataset

**Internet Archive `feature_films` collection** —
https://archive.org/details/feature_films

- **28,468 entries** (a few dozen are subcollections like `Film_Noir`,
  `SciFi_Horror`, `silent_films`; the rest are films). Public domain /
  CC; no account, no token, no gate — plain HTTP, like the AMI mirror.
- Duration profile (probed sample of top-downloaded items, 2026-08-21):
  **86% ≥ 60 min**, median 82 min, p25 70 / p75 99, outliers to 11 h.
  Usable 1-hour-plus pool after filters: **thousands**.
- Most films are talkies → real speech for the dual-lane (Whisper) pipe;
  silent-era films exist and are filtered out (§4).
- Each film is an archive.org **item** carrying several derivatives of
  the same print. We take the modern **h.264 MP4** (~0.4 GB/h; fall back
  `MPEG4` when h.264 is absent). Verified example, `his_girl_friday`
  (1940, 91.7 min):

  ```
  his_girl_friday.mp4        h.264        5504 s   575 MB   ← what we stage
  his_girl_friday.mpeg       MPEG2        5504 s  3292 MB
  his_girl_friday.ogv        Ogg Video    5504 s   390 MB
  his_girl_friday_512kb.mp4  512Kb MPEG4  5504 s   401 MB
  ```

**The three public APIs** (all JSON, no auth):

| purpose | URL |
|---|---|
| enumerate the collection | `https://archive.org/services/search/v1/scrape?q=collection:feature_films&count=10000` (cursor-paginated) |
| inspect one item | `https://archive.org/metadata/<identifier>` → files with `format`, `length` (seconds), `size` |
| download a file | `https://archive.org/download/<identifier>/<filename>` (302 → datanode, then plain HTTP, ~5–20 MB/s) |

Because item metadata already carries per-file duration, the census and
selection (§3) download **zero video bytes** — the same trick as the AMI
HEAD sweep in `ami_eda/`.

## 2. The corpus contract

One corpus doc = one film = `<identifier>.mp4` in
`s3://rocketride-benchmark-data/leela/corpus/archive_films/`, plus
`corpus_manifest.json` with, for every doc:

- `duration_s` — from the census metadata (the footage denominator)
- `video_duration_s` — ffmpeg-probed video stream (the frame_law
  denominator; AMI taught us A/V stream lengths disagree)
- `sha256` + `bytes` — content identity (arms the corpus_pin gate)
- the selection rule, so the corpus is reconstructible from scratch

The pinned selection list `corpus/sets/archive_films.txt`
(identifier `<TAB>` filename) and
`corpus/sets/archive_films_durations.json` are committed to the repo —
they ARE the corpus definition; S3 is just the staged bytes.

## 3. Step one — census + selection (`corpus/census_archive_films.py`)

Runs anywhere (Mac is fine — metadata only, ~30–60 min at 12 threads,
resumable; re-runs skip already-censused items).

```bash
cd aws_videobench
python3 corpus/census_archive_films.py --n 500     # census + pin 500 films
# or:  --census-only        just build census_archive_films.jsonl
#      --select-only --n N  re-pin from the existing census
```

What it does:

1. **CENSUS** — scrape-API enumeration (`mediatype:movies`, which
   excludes the subcollection entries) → per-item metadata → one JSONL
   line per item in `corpus/census_archive_films.jsonl` (the full-
   collection EDA record: title, year, downloads, best mp4 derivative,
   duration, size; dark items flagged).
2. **SELECT** — eligibility filters, then pin:
   - duration **60–240 min** (the 1-hour-plus requirement; capped so a
     10-hour outlier can't distort wave sizing)
   - has an h.264 (preferred) or MPEG4 `.mp4` derivative
   - **not in `silent_films`** (dual-lane needs speech; a second,
     per-file audio gate runs at staging)
   - **deduped by normalized title** (the collection has re-uploads)
   - rank by all-time downloads (popularity ≈ print-quality proxy;
     snapshot recorded in the file header), take top N.

   It prints the filtered-out counts and the eligible-pool size, so you
   see the real census **before staging a single video byte**. Commit
   `corpus/sets/` afterwards — the pin is the contract.

## 4. Step two — staging to S3 (`run/stage_archive_films.sh`)

Runs ON THE BOX (direct archive.org → box → S3; nothing routes through
the laptop; the box instance role already has the S3 permissions — it
staged `ami_full`). Identical discipline to `stage_corpus.sh` v2:

```
for each (identifier, filename) in archive_films.txt:
  skip     if already in S3 AND probed (resume state: ~/stage_films_shas.jsonl + S3 listing)
  fetch    S3 copy if staged (resume), else curl from archive.org (retries, redirect-following)
  gate     ffprobe MUST find an audio stream — silent prints SKIPPED + logged
  probe    video-stream duration via ffmpeg null-mux  (frame_law)
  record   sha256 + bytes + video_duration_s          (corpus_pin)
  upload   aws s3 cp → $S3_CORPUS/<identifier>.mp4
  clean    delete the local copy   (delete-as-you-go: peak disk = ONE film, <4 GB)
then: build corpus_manifest.json (durations + shas + skip list) → S3
```

Launch (from the laptop, via the usual box control):

```bash
aws sso login --profile leela
bash aws_bench/local/box.sh start
bash aws_bench/local/box.sh run 'cd ~/bench_langgraph_prod && git pull --ff-only origin aws-bench'
bash aws_bench/local/box.sh launch stagefilms \
  'cd ~/bench_langgraph_prod/aws_videobench && bash run/stage_archive_films.sh'
bash aws_bench/local/box.sh tail stagefilms
```

Or directly on the box:
`nohup bash run/stage_archive_films.sh > ~/logs/stage_films.log 2>&1 < /dev/null &`

Expectations for N=500: ≈ 700 footage-hours, **≈ 300 GB**, an overnight
run (sequential on purpose — polite to archive.org, and resume makes
interruption free). S3 cost ≈ **$7/month**. `S3_CORPUS` env overrides
the destination for test sets.

## 5. Step three — verify

```bash
aws s3 ls s3://rocketride-benchmark-data/leela/corpus/archive_films/ --profile leela | grep -c '.mp4$'
aws s3 cp s3://.../archive_films/corpus_manifest.json - --profile leela | python3 -m json.tool | head -30
```

- object count == manifest `n_docs` (+ the manifest's `skipped_no_audio`
  list explains any shortfall vs the pinned N)
- spot-check 2–3 shas against fresh downloads
- spot-play 2–3 films (`ffprobe` streams: 1 video + 1 audio)

## 6. Step four — smoke, then waves

1. **`archive10` smoke** (10 films through both arms, one rep) — the
   real risks, none of which AMI exercised:
   - vintage audio through Whisper (music-heavy, low-fidelity mixes)
   - old-print codecs/edge cases inside the h.264 derivatives
   - `.mp4` routing to the webhook's video lane (AMI only proved `.avi`)
   - chunk mass at 15 s frame interval: a 90-min film ≈ **360 frames**
     ≈ 2.6× an AMI meeting — watch splitter/chunk behavior
2. **Full runs** — `run_waves.sh` unchanged, corpus prefix swapped;
   waves of **30–40 films** (≈ 20–25 GB) fit the 30 GB `/dev/shm` cap
   and keep RR's scratch retention bounded, teardown per wave as always.
3. Runtime bounds at AMI-measured rates (700 h): RR blast ~19 h/rep
   (K=5 engines ≈ 4 h), LG ~4.7 h/rep. Long-doc regime: per-video cost
   dominates, per-request overhead is noise — the mirror image of a
   small-doc corpus.

## 7. Where everything lives

| thing | location |
|---|---|
| census (full-collection EDA) | `aws_videobench/corpus/census_archive_films.jsonl` |
| pinned selection (the corpus definition) | `aws_videobench/corpus/sets/archive_films.txt` + `archive_films_durations.json` |
| census/selection script | `aws_videobench/corpus/census_archive_films.py` |
| stager | `aws_videobench/run/stage_archive_films.sh` |
| canonical corpus | `s3://rocketride-benchmark-data/leela/corpus/archive_films/` (+ manifest) |
| resume state (box) | `~/stage_films_shas.jsonl` (+ the S3 listing itself) |
| box control (laptop) | `aws_bench/local/box.sh` — start/stop/run/launch/tail |
| source survey / why this dataset | `aws_videobench/LONG_VIDEO_SOURCES.md` |

## 8. Known traps

1. **Dark items** — some identifiers return metadata without files
   (withdrawn); the census flags them, selection skips them.
2. **Subcollections appear as search results** unless you filter
   `mediatype:movies` (the census does).
3. **Silent films** — excluded by subcollection at census time AND by
   the ffprobe audio gate at staging; skips are logged in the manifest,
   so the staged count can be slightly under N (pin a few spares with
   `--n` if the exact count matters).
4. **Re-uploads/duplicates** — title-level dedupe in the census; same
   film from different years (remakes) is deliberately kept.
5. **Be polite to archive.org** — the stager is sequential with retries;
   don't fan it out. ~5–20 MB/s per stream is normal.
6. **The box idle-watchdog stops the box during network-bound work**
   (the AMI staging lesson) — always `nohup`-detach; relaunch resumes at
   the first unstaged film.
7. **`aws` vanishes from PATH in nohup shells** — the script resolves
   `~/.local/bin/aws` explicitly, like all the others.
8. **Licensing** — the collection is public-domain/CC material, but the
   S3 bucket stays private and the corpus is for internal benchmarking;
   the manifest records identifiers so provenance is always traceable.
