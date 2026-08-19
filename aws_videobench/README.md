# aws_videobench — RocketRide video pipeline on the AWS box

Video sibling of `aws_bench/` (the PDF benchmark). Same engine, same two
3.3.1 patches, same client-in-its-own-container discipline. Currently at the
**smoke stage**: one AMI meeting video through the pipe, twice, with explicit
verdicts on the risks the video pipe carries. Not a benchmark yet — no
envelope, no LangGraph arm, no metrics gates.

```
aws_videobench/
  pipe/benchmark_video.pipe   THE pipeline contract (canonical copy — moved
                              out of aws_bench so exactly one copy exists)
  corpus/fetch_ami.sh         AMI corpus: Closeup1.avi + Mix-Headset.wav
                              muxed per meeting (the mirror's .avi has NO
                              audio track), manifest + SHA256SUMS
  engine/                     RocketRide 3.3.1, SHA-pinned, boot fix +
                              BUG_CHUNK_DUPLICATION patch (same as PDF arm)
  smoke/smoke_video.py        1 video x N reps -> R1..R6 verdicts
  run/smoke_run.sh            fetch -> build -> up -> smoke -> S3
  results/                    run output (git-ignored)
```

## The pipeline

```
                 ┌ video ► audio_transcribe (faster-whisper base) ─ text ┐
webhook ─ fan-out┤                                                       ├► preprocessor ► embedding(miniLM) ► response_documents
                 └ video ► frame_grabber (15s) ─ image ► detect (RF-DETR) ─ text ┘
```

All components run locally in the engine (whisper via faster-whisper/PyAV,
frame decode via the engine's own AVI reader + imageio_ffmpeg, RF-DETR via
the `rfdetr` package). Nothing calls an external API. The tail (preprocessor,
miniLM 384-dim, response_documents) is byte-identical to the PDF pipe, so the
structure gate and `cpu_s_per_chunk` carry over.

## Run the smoke (on the box)

```bash
cd ~/bench_langgraph_prod && git pull
cd aws_videobench
nohup bash run/smoke_run.sh > ~/logs/videosmoke.log 2>&1 < /dev/null &
```

~100 MB AMI download, then rep 1 includes the engine pip-installing the
whisper + rfdetr stacks and pulling model weights into the `rr-model-cache`
volume (one-time per box, several GB). Rep 2 is the honest single-doc time.

## What the smoke decides (R1–R6)

| verdict | meaning if not PASS |
|---|---|
| R1 lane routing | webhook did not put `.avi` on the `video` lane → insert `parse` (tags→video) into the pipe |
| R2 audio demux+ASR | faster-whisper can't read PCM-in-AVI → re-mux corpus to MKV |
| R3 frame lane (WARN) | no detection-like chunks visible → check `docs_rep*.json` metadata for lane provenance before concluding |
| R4 structure | 384-dim/finite/norm gate broken → engine issue, stop |
| R5 content determinism | whisper temperature-fallback sampled → must pin/mitigate before any benchmark rep counts |
| R6 order determinism (WARN) | two-lane merge interleaves → the benchmark determinism gate needs sorted chunk-hash comparison |

## Toward the real benchmark

Sequence after a green smoke: N=20 RocketRide-only run for corpus sizing →
LangGraph arm (faster-whisper + ffmpeg/OpenCV + `rfdetr` + same splitter +
same miniLM) → matched runs under `aws_bench`'s envelope rules (shared
cpuset, OMP=1, one deadline, client on its own cores, provenance or it
didn't happen).
