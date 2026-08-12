# Containerized benchmark arms

Both arms run as containers with the **same architecture and the same resource
envelope**. That symmetry is the whole point: a framework comparison where one
side gets more CPU, more memory, or a different architecture measures the
environment, not the frameworks.

| | LangGraph arm | RocketRide arm |
| --- | --- | --- |
| image | `prodbench-langgraph:latest` | `prodbench-rocketride:latest` |
| build context | `langgraph-fastapi/` | `rocketride/` |
| transport | HTTP, `:8100` | WebSocket DAP, `:5565` |
| process shape | one uvicorn worker | one engine process (+ one per launched pipe) |
| platform | `linux/amd64` | `linux/amd64` |
| limits | 2.0 CPU / 4 GB | 2.0 CPU / 4 GB |

## Run

```bash
docker compose up -d --build       # both arms
docker compose ps                  # health is warm-up gated on both
docker compose logs -f langgraph
docker compose down
```

LangGraph arm:

```bash
curl -s localhost:8100/health/ready
curl -s localhost:8100/meta | python3 -m json.tool
curl -s -i -F "file=@langgraph-fastapi/tests/fixtures/text_page.pdf;type=application/pdf" \
  localhost:8100/v1/process/document-pdf-v1
```

RocketRide arm — the default command boots the engine as a service; override
it to run the Phase R probe instead:

```bash
docker compose run --rm rocketride probe
```

## Why each image is built the way it is

### LangGraph (`langgraph-fastapi/Dockerfile`)

- **The embedding model is baked into the image at build time.** Runtime then
  sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, so a cache miss fails
  loudly instead of silently downloading possibly-different weights mid-run.
  Every container embeds with byte-identical weights.
- **Thread pins are `ENV`, not entrypoint flags.** `OMP/MKL/OPENBLAS_NUM_THREADS=1`
  and `TOKENIZERS_PARALLELISM=false` must be set before torch initializes;
  `configure_runtime()` re-applies them and `/meta` reports what was applied.
- **`pypdf` is pinned exactly (`==6.15.0`).** It defines the offline reference,
  so a float would silently invalidate parity artifacts.
- **One uvicorn worker** is the deployment shape, not a default.
- `linux/amd64` because the RocketRide engine ships linux-x64 only and the arms
  must match.

### RocketRide (`rocketride/Dockerfile`)

- **The engine is downloaded from its pinned GitHub release and checksummed.**
  Note `ENGINE_SHA256` is the hash of the **extracted `engine` binary**
  (`cf1fbf9c…`), not of the tarball — that is what the prior harness pins and
  what actually determines behavior. The archive is flat (`ai/`, `engine`,
  `lib/`, `nodes/`, …), so no `--strip-components`.
- **`libc++1` / `libc++abi1`** are hard runtime dependencies of the C++ engine.
- **No `api.rocketride.ai`.** `ROCKETRIDE_URI` points at `127.0.0.1:5565` and
  `ROCKETRIDE_APIKEY` is the local-dev literal. Nothing leaves the container.
- **SDK pinned** to `rocketride==1.3.0`, the version Phase R verified against.

## Caveats you must not lose

- **On an arm64 Mac both containers run emulated.** Correctness only. No timing
  produced on this host is ever reportable — the same caveat RR_BENCH's own
  `RESULTS.md` carries. A native x86-64 host is still owed before any ratio is
  quotable.
- **Resource limits are a matched pair.** Changing one without the other
  invalidates comparability. Record any change alongside results.
- The RocketRide engine spawns roughly one ~640 MB process per launched pipe,
  so the 4 GB limit bounds concurrent pipes. If a run needs more pipes, raise
  **both** arms' limits together.
