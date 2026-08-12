"""Merge per-arm records, compute cross-arm comparisons, write summary.md.

Usage (LangGraph venv python):
  langgraph-fastapi/.venv/bin/python results/compare_summarize.py results
"""

import json
import statistics as st
import sys
from pathlib import Path

TRUNCATION_CHARS = 3500  # observed saturation point of the 512-token embedder


def load(path):
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            r = json.loads(line)
            rows[r["doc"]] = r  # last write wins (resume-safe)
    return rows


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def pct(x, n):
    return f"{x}/{n} ({100*x/n:.1f}%)" if n else "n/a"


def main(results_dir: str) -> int:
    out = Path(results_dir)
    rr = load(out / "per_doc_rr.jsonl")
    lg = load(out / "per_doc_lg.jsonl")
    docs = sorted(set(rr) | set(lg))
    n = len(docs)

    # merged per_doc.jsonl (deliverable 3)
    with open(out / "per_doc.jsonl", "w") as fh:
        for d in docs:
            for arm in (rr, lg):
                if d in arm:
                    fh.write(json.dumps(arm[d]) + "\n")

    rr_ok = [d for d in docs if rr.get(d, {}).get("ok")]
    lg_ok = [d for d in docs if lg.get(d, {}).get("ok")]
    both_ok = [d for d in docs if d in set(rr_ok) and d in set(lg_ok)]

    failures = []
    for d in docs:
        for arm_name, arm in (("rocketride", rr), ("langgraph", lg)):
            r = arm.get(d)
            if r is None:
                failures.append((arm_name, d, "no record"))
            elif not r.get("ok"):
                failures.append((arm_name, d, r.get("error", "unknown")))

    # cross-arm deltas on both-ok docs
    char_ratios, count_deltas, hash_doc_matches, hash_chunk_matches = [], [], 0, [0, 0]
    trunc = {"rocketride": 0, "langgraph": 0}
    chunks_total = {"rocketride": 0, "langgraph": 0}
    outliers = []
    for d in both_ok:
        a, b = rr[d], lg[d]
        for arm_name, r in (("rocketride", a), ("langgraph", b)):
            chunks_total[arm_name] += r["n_chunks"]
            trunc[arm_name] += sum(1 for L in r["chunk_lens"] if L > TRUNCATION_CHARS)
        if b["total_chars"] > 0:
            char_ratios.append((d, a["total_chars"] / b["total_chars"]))
        count_deltas.append(a["n_chunks"] - b["n_chunks"])
        if a["chunk_sha256"] == b["chunk_sha256"]:
            hash_doc_matches += 1
        inter = len(set(a["chunk_sha256"]) & set(b["chunk_sha256"]))
        hash_chunk_matches[0] += inter
        hash_chunk_matches[1] += min(a["n_chunks"], b["n_chunks"])

    ratios = [r for _, r in char_ratios]
    med_ratio = st.median(ratios) if ratios else None
    for d, r in char_ratios:
        if med_ratio and abs(r - med_ratio) > 0.15:
            outliers.append((d, round(r, 3)))

    # cosine on aligned chunks (index-aligned), from raw vector dumps
    cos_vals = []
    raw_rr, raw_lg = out / "raw", out / "raw_lg"
    for d in both_ok:
        fa, fb = raw_rr / f"{Path(d).stem}.json", raw_lg / f"{Path(d).stem}.json"
        if not (fa.exists() and fb.exists()):
            continue
        va = json.loads(fa.read_text())["vectors"]
        vb = json.loads(fb.read_text())["vectors"]
        for x, y in zip(va, vb):
            if x and y and len(x) == len(y):
                cos_vals.append(cos(x, y))

    norm_bad = {arm_name: sum(
        1 for d in both_ok for nn in {"rocketride": rr, "langgraph": lg}[arm_name][d]["l2_norms"]
        if abs(nn - 1.0) > 1e-3
    ) for arm_name in ("rocketride", "langgraph")}
    models = {
        "rocketride": sorted({rr[d].get("model_id") for d in rr_ok if rr[d].get("model_id")}),
        "langgraph": sorted({lg[d].get("model_id") for d in lg_ok if lg[d].get("model_id")}),
    }
    dims = sorted({r.get("vector_dim") for r in list(rr.values()) + list(lg.values())
                   if r.get("ok") and r.get("vector_dim")})
    sha_hdr_fail = [d for d in lg_ok if lg[d].get("sha_header_ok") is False]

    S = []
    S.append("# 200-document benchmark run — RocketRide vs LangGraph\n")
    S.append("Corpus: Govdocs1 threads 000+001, first 200 valid PDFs "
             "(see `manifest.jsonl` for the selection rule and hashes; "
             "`dropped.jsonl` for exclusions).\n")
    S.append("> **Environment caveat:** both arms ran linux/amd64 CONTAINERS "
             "EMULATED on an arm64 Mac. No wall-clock timing in this report; "
             "raw values sit in `per_doc*.jsonl` marked "
             "`wall_s_emulated_not_reportable`.\n")

    S.append("## Success rates\n")
    S.append(f"- RocketRide: {pct(len(rr_ok), n)}")
    S.append(f"- LangGraph:  {pct(len(lg_ok), n)}")
    S.append(f"- Both arms succeeded: {pct(len(both_ok), n)}")
    S.append(f"- LangGraph `X-Output-SHA256` verified on every 200: "
             f"{'yes' if not sha_hdr_fail else f'FAILED on {sha_hdr_fail}'}\n")

    S.append("## Model / vector sanity\n")
    S.append(f"- model id per arm: {json.dumps(models)}")
    S.append(f"- vector dims observed: {dims}")
    S.append(f"- L2 norms off unit (>1e-3): rocketride={norm_bad['rocketride']}, "
             f"langgraph={norm_bad['langgraph']}\n")

    S.append("## Cross-arm chunking comparison (the same-work gate)\n")
    S.append(f"- whole-doc chunk-hash match: {pct(hash_doc_matches, len(both_ok))}")
    S.append(f"- per-chunk hash matches: {pct(hash_chunk_matches[0], hash_chunk_matches[1])}")
    S.append("- Expected ≈0: the arms run different extractors (RocketRide `parse` "
             "vs pypdf) by accepted methodology, and RocketRide's parser "
             "duplicates ~3.3% of lines (Phase R finding). Hash mismatch here is "
             "the extractor delta being visible — it is NOT an embedding bug.\n")
    if count_deltas:
        S.append(f"- chunk-count delta (RR−LG): min={min(count_deltas)}, "
                 f"median={st.median(count_deltas)}, max={max(count_deltas)}")
    if ratios:
        S.append(f"- total-char ratio RR/LG: median={med_ratio:.3f}, "
                 f"p10={sorted(ratios)[int(0.1*len(ratios))]:.3f}, "
                 f"p90={sorted(ratios)[int(0.9*len(ratios))]:.3f} "
                 f"(baseline expectation: RR slightly higher from duplicated lines)")
    S.append(f"- docs deviating >0.15 from median ratio: {len(outliers)}")
    for d, r in outliers[:15]:
        S.append(f"    - {d}: ratio {r}")
    S.append("")

    S.append("## Cosine similarity of index-aligned chunks — **read caveat first**\n")
    S.append("> **Caveat 1 (mandatory):** vector parity is WEAK evidence for "
             f"chunks >~3000–3500 chars. The embedder truncates at 512 tokens, so "
             "the tail of long chunks is never embedded; two arms with different "
             "chunk text can produce numerically identical vectors (smoke test: "
             "839-char text difference, vectors matched to 1.5e-8). Chunk/offset "
             "hashes above are the real gate; cosine below is reported for "
             "completeness only.\n")
    if cos_vals:
        S.append(f"- aligned chunk pairs compared: {len(cos_vals)}")
        S.append(f"- cosine: median={st.median(cos_vals):.4f}, "
                 f"mean={st.mean(cos_vals):.4f}, min={min(cos_vals):.4f}, "
                 f"p10={sorted(cos_vals)[int(0.1*len(cos_vals))]:.4f}\n")

    S.append("## Truncation exposure (caveat 2 — correctness finding)\n")
    S.append(f"- chunks exceeding {TRUNCATION_CHARS} chars (embedder saturated, "
             "tail unsearchable):")
    for arm_name in ("rocketride", "langgraph"):
        S.append(f"    - {arm_name}: {pct(trunc[arm_name], chunks_total[arm_name])} of chunks")
    S.append("- `chunk_size` stays pinned (caveat 3): it mirrors RocketRide's "
             "ignored-config behavior; open methodology item for Leela + Shashi "
             "in `langgraph-fastapi/toil.md`.\n")

    S.append("## Failures\n")
    if failures:
        for arm_name, d, err in failures:
            S.append(f"- [{arm_name}] {d}: {err}")
    else:
        S.append("- none")
    S.append("")

    (out / "summary.md").write_text("\n".join(S))
    print("\n".join(S))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
