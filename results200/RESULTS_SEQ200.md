# Sequential 200-PDF results — RocketRide (tonight) vs LangGraph (archive)

> **Labels that apply to every number here:** emulated x86-on-arm64
> (relative shapes only, no absolute claims); single pass each arm (no
> repeatability claims); and the two arms ran under DIFFERENT conditions —
> LG on Aug 7 (2 CPU / 4 GB, pre-fix image), RR tonight (12 CPU / 12 GB,
> pooled client). Timing comparison is indicative, not matched. Work
> metrics (chunks/chars/hashes) are condition-independent and solid.
> Both runs: same 200 documents, same deterministic order, concurrency 1.


> **ATTRIBUTION CAVEAT (added 2026-08-10, after operator challenge):** every
> wedge observation in this project occurred under x86-on-ARM emulation.
> The stuck-jspawnhelper + livelock signature is consistent BOTH with a
> product defect AND with known emulation pathologies (fork/exec of a
> ~300-thread translated process; lock-contention degradation under
> translation). Wedge findings are therefore "reproducible in this
> environment," NOT attributed to the product, until reproduced on a native
> Linux x64 host. RocketRide ships no linux-arm64 build, so no fair native
> containerized comparison exists on Apple Silicon. The deterministic
> per-doc empty-result failures (000164/000357) are likely attribution-safe
> (clean responses, not stress behavior) but also deserve native retest.

## Completion

| | LangGraph | RocketRide |
|---|---|---|
| completed | **200/200** | **140/175 attempted** |
| genuine per-doc failures | 0 | 2 (`000164`, `000357` — engine returns empty result, no error) |
| wedge | none | **pipe wedged at `000744.pdf`; 33 subsequent docs were 300 s timeouts (not per-doc results); run stopped 26 docs early** |

The wedge is the third independent reproduction of the Phase-R finding, but
see the CORRECTION below: some document kills the shared pipeline silently — the WebSocket stays
healthy, no error is surfaced, and every later doc times out. This driver
had no relaunch-recovery by design (ground-truth capture). `000164` failing
with "no documents" reproduces its identical failure from the Aug 7 run —
deterministic per-doc failure, now on two different engine sessions.

**CORRECTION (2026-08-10):** `000163.pdf`, which wedged the Aug 7 run, was
processed SUCCESSFULLY in this run — the wedge here happened at `000744.pdf`
instead. So wedging is **not a deterministic property of a particular
document**; it depends on engine state/load history. You cannot blocklist
your way out of it. Full wedge sequence from `000744` onward: 33 records,
`T N T×31` (timeout, no-documents, then 31 straight timeouts), zero
recoveries. Details in `CONTEXT_SNAPSHOT.md` §4.1/§4.3.

## Service latency, ok docs only (seconds, emulated, differently-conditioned)

| percentile | LangGraph (2 CPU) | RocketRide (12 CPU) |
|---|---|---|
| p50 | 1.46 | 1.83 |
| p90 | 9.32 | 11.36 |
| p99 | 34.2 | **141.8** |
| max | 41.9 | 144.6 |
| mean | 3.12 | 6.33 |

Median service times are comparable; the tails are not. RocketRide's p99 is
~4× LangGraph's — heavyweight documents (e.g. `000157`, `000672`: ~140 s each)
cost RR far more than LG's worst doc (41.9 s), despite RR having 6× the CPU
allocation in tonight's run.

## Sequential throughput (concurrency 1 — this is 1/mean-latency, not a load test)

- LangGraph: **0.321 docs/s** (200 docs, zero failures)
- RocketRide: **0.158 docs/s** over its healthy work time (140 ok docs)

## Work comparison (140 docs both arms completed)

- **Chunk-count delta (RR−LG): median 0** (min −7, max +142 on one outlier doc)
- **Total-char ratio RR/LG: median 0.994** (p10 0.971, p90 1.030) — on real
  Govdocs PDFs the extractors produce near-identical volume on the median
  doc, ±3% spread. The +4.7% duplication seen in Phase R was measured on a
  synthetic fixture; on this corpus the per-doc differences roughly cancel.
- **Chunk-hash overlap: 2/1434** — expected ≈0 (different extractors,
  accepted methodology). Hashes remain the per-arm gate, not cross-arm.
- **Model identity**: every RR doc reports
  `sentence-transformers/multi-qa-MiniLM-L6-cos-v1`; LG pins the same.
  Vectors 384-dim, L2-normalized both sides.

## What this does NOT tell you

Concurrency behavior (levels 4/16/64 were cancelled), repeatability (single
pass), native performance (emulation), or a fair timing ratio (conditions
differ). The cancelled stepped run remains the way to answer the
RR-threadCount-64 question.
