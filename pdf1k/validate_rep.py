"""Rep validity gates + fixed metric definitions. Emits one JSON object.

Gates (fixed):
  all docs returned & unique; vectors finite, dim 384, norms ≈ 1;
  LG: chunk hashes + offsets byte-exact vs offline ground truth;
  RR: chunk hashes byte-consistent vs the FIRST valid RR rep (rep-over-rep
      determinism; cross-extractor byte-equality is impossible by design).

Metrics (fixed, never varied): batch_throughput = n_docs/(last completion −
first submit); successful-doc throughput reported separately; TTFR =
min(first_result) − min(submit); batch-position percentiles include queueing.
"""

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "results" / "ground_truth" / "per_doc.jsonl"


def main(rep_dir, arm, n_expected):
    rep = Path(rep_dir)
    rows = []
    meta = None
    for line in (rep / "per_doc.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "client_meta":
            meta = r
        else:
            rows.append(r)

    out = {"arm": arm, "n_records": len(rows), "n_expected": int(n_expected)}
    docs = [r["doc"] for r in rows]
    out["all_returned"] = len(rows) == int(n_expected)
    out["ids_unique"] = len(set(docs)) == len(docs)
    ok_rows = [r for r in rows if r.get("ok")]
    out["n_ok"] = len(ok_rows)

    out["dims_ok"] = all(r.get("vector_dim") == 384 for r in ok_rows if r.get("n_chunks"))
    out["finite_ok"] = all(r.get("vectors_finite", True) for r in ok_rows)
    out["norms_ok"] = all(
        abs(r["l2_norms_minmax"][0] - 1) < 1e-3 and abs(r["l2_norms_minmax"][1] - 1) < 1e-3
        for r in ok_rows if r.get("l2_norms_minmax") and r.get("n_chunks")
    )

    if arm == "lg":
        gt = {}
        for line in GT.read_text().splitlines():
            g = json.loads(line)
            if g.get("ok"):
                gt[g["doc"]] = g
        mism = []
        for r in ok_rows:
            g = gt.get(r["doc"])
            if not g:
                continue
            if r["chunk_sha256"] != g["chunk_sha256"] or r.get("offsets") != g["offsets"]:
                mism.append(r["doc"])
        out["gt_hash_mismatches"] = mism[:20]
        out["gt_exact"] = not mism
        gate_extra = out["gt_exact"]
    else:
        base = ROOT / "runs" / "pdf1k" / "rr_baseline_hashes.json"
        cur = {r["doc"]: r["chunk_sha256"] for r in ok_rows}
        if base.exists():
            prior = json.loads(base.read_text())
            mism = [d for d in cur if d in prior and prior[d] != cur[d]]
            out["consistency_mismatches"] = mism[:20]
            out["consistent_with_baseline"] = not mism
            gate_extra = not mism
        else:
            base.write_text(json.dumps(cur))
            out["baseline_written"] = True
            gate_extra = True

    out["valid"] = bool(
        out["all_returned"] and out["ids_unique"] and out["n_ok"] == int(n_expected)
        and out["dims_ok"] and out["finite_ok"] and out["norms_ok"] and gate_extra
    )

    subs = [r["submit_ns"] for r in rows if "submit_ns" in r]
    comps = [r["completion_ns"] for r in rows if "completion_ns" in r]
    if subs and comps:
        span_s = (max(comps) - min(subs)) / 1e9
        lat = sorted((r["completion_ns"] - r["submit_ns"]) / 1e9
                     for r in rows if "completion_ns" in r and "submit_ns" in r)
        okc = [r["completion_ns"] for r in ok_rows if "completion_ns" in r]
        out["metrics_emulated_relative_only"] = {
            "batch_span_s": round(span_s, 1),
            "batch_throughput_docs_s": round(len(rows) / span_s, 3),
            "successful_doc_throughput_docs_s":
                round(len(ok_rows) / span_s, 3) if span_s else None,
            "ttfr_s": round((min(okc) - min(subs)) / 1e9, 2) if okc else None,
            "batch_position_latency_s": {
                "p50": round(lat[len(lat)//2], 1),
                "p90": round(lat[int(len(lat)*0.9)], 1),
                "p99": round(lat[int(len(lat)*0.99)], 1),
                "note": "includes queueing (open-loop burst)",
            },
            "send_window_s": meta.get("send_window_task_creation_s") if meta else None,
        }
        if meta and arm == "lg":
            out["client"] = {k: meta[k] for k in
                             ("client_cpu_user_s", "client_cpu_sys_s", "client_maxrss_mb")
                             if k in meta}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
