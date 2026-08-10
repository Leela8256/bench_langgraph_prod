"""Per-level validation + fixed metrics for PDF-200.

Gates: all N completed-or-explicitly-failed (zero silent), IDs unique,
chunk hashes byte-exact vs THAT ARM's ground truth, dims/finiteness/norms.
Metrics (fixed): level throughput = N/(last completion - first submit);
closed-loop latency percentiles are TRUE service latencies.

  validate200.py <level_dir> <arm:lg|rr> <n_docs>
"""

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT_FILES = {
    "lg": ROOT / "results200" / "ground_truth_lg.jsonl",
    "rr": ROOT / "results200" / "ground_truth_rr.jsonl",
}


def main(level_dir, arm, n_expected):
    n_expected = int(n_expected)
    rows, meta = [], None
    for line in (Path(level_dir) / "per_doc.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "level_meta":
            meta = r
        else:
            rows.append(r)

    gt = {}
    gt_path = GT_FILES[arm]
    if gt_path.exists():
        for line in gt_path.read_text().splitlines():
            g = json.loads(line)
            gt[g["doc"]] = g

    ok_rows = [r for r in rows if r.get("ok")]
    docs = [r["doc"] for r in rows]
    hash_mism = [r["doc"] for r in ok_rows
                 if r["doc"] in gt and r["chunk_sha256"] != gt[r["doc"]]["chunk_sha256"]]
    silent = n_expected - len(rows)

    out = {
        "arm": arm,
        "level": meta["level"] if meta else None,
        "n_records": len(rows),
        "n_ok": len(ok_rows),
        "n_failed_explicit": len(rows) - len(ok_rows),
        "n_silent": silent,
        "ids_unique": len(set(docs)) == len(docs),
        "gt_docs_available": len(gt),
        "gt_hash_mismatches": hash_mism[:15],
        "dims_ok": all(r.get("vector_dim") == 384 for r in ok_rows if r.get("n_chunks")),
        "norms_ok": all(
            abs(r["l2_norms_minmax"][0] - 1) < 1e-3 and abs(r["l2_norms_minmax"][1] - 1) < 1e-3
            for r in ok_rows if r.get("l2_norms_minmax") and r.get("n_chunks")),
        "identity_ok": all(r.get("identity_ok", True) for r in ok_rows),
    }
    out["valid"] = bool(
        silent == 0 and out["ids_unique"] and not hash_mism
        and out["dims_ok"] and out["norms_ok"] and out["identity_ok"]
        and len(ok_rows) == n_expected
    )
    # a level where every doc failed explicitly is complete-but-invalid;
    # distinguish that from gate-level corruption
    out["all_docs_accounted"] = silent == 0

    subs = [r["submit_ns"] for r in rows if "submit_ns" in r]
    comps = [r["completion_ns"] for r in rows if "completion_ns" in r]
    if subs and comps and ok_rows:
        span = (max(comps) - min(subs)) / 1e9
        lat = sorted((r["completion_ns"] - r["submit_ns"]) / 1e9 for r in ok_rows)
        okc = [r["completion_ns"] for r in ok_rows]
        out["metrics_emulated_relative_only"] = {
            "level_span_s": round(span, 2),
            "level_throughput_docs_s": round(len(rows) / span, 3),
            "successful_throughput_docs_s": round(len(ok_rows) / span, 3),
            "ttfr_s": round((min(okc) - min(subs)) / 1e9, 3),
            "service_latency_s": {
                "p50": round(lat[len(lat) // 2], 3),
                "p90": round(lat[int(len(lat) * 0.9)], 3),
                "p99": round(lat[min(int(len(lat) * 0.99), len(lat) - 1)], 3),
                "note": "closed-loop TRUE service latency (not batch-position)",
            },
        }
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
