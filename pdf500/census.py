"""PDF-500 census + post-run validation for one arm.

Implements the handoff's fixed metric definitions and validation steps 1
(reconciliation), 3 (gates by tier), 4 (duplicate/loss). Emits one JSON doc.

  census.py <per_doc.jsonl> <arm:rr|lg> <n_offered> <gt_file>
"""

import json
import statistics as st
import sys
from pathlib import Path

EXPECTED_FAIL = {"000164.pdf", "000357.pdf"}


def main(path, arm, n_offered, gt_file):
    n_offered = int(n_offered)
    rows, meta, wedges = [], None, []
    for line in Path(path).read_text().splitlines():
        r = json.loads(line)
        k = r.get("kind")
        if k == "shot_meta" or k == "level_meta":
            meta = r
        elif k == "wedge_event":
            wedges.append(r)
        else:
            rows.append(r)

    gt = {}
    if Path(gt_file).exists():
        for line in Path(gt_file).read_text().splitlines():
            g = json.loads(line)
            gt[g["doc"]] = g

    docs = [r["doc"] for r in rows]
    ok = [r for r in rows if r.get("ok")]
    fails = [r for r in rows if not r.get("ok")]
    by_reason = {}
    for r in fails:
        reason = r.get("reason") or ("timeout" if "timeout" in (r.get("error") or "")
                                     else "completion_proof_missing")
        by_reason.setdefault(reason, []).append(r["doc"])

    # gates by tier
    byte_tier = [r for r in ok if r["doc"] in gt]
    byte_pass = [r for r in byte_tier if r["chunk_sha256"] == gt[r["doc"]]["chunk_sha256"]]
    struct_tier = [r for r in ok if r["doc"] not in gt]
    struct_pass = [r for r in struct_tier
                   if r.get("identity_ok", True) and r.get("vector_dim") == 384
                   and r.get("l2_norms_minmax")
                   and abs(r["l2_norms_minmax"][0] - 1) < 1e-3
                   and abs(r["l2_norms_minmax"][1] - 1) < 1e-3]
    expected_fail_observed = {d: next((r.get("reason") for r in fails if r["doc"] == d), "COMPLETED")
                              for d in EXPECTED_FAIL if d in docs}

    out = {
        "arm": arm,
        "census": {
            "offered": n_offered,
            "records": len(rows),
            "reconciled": len(rows) == n_offered,
            "unique_ids": len(set(docs)) == len(docs),
            "completed": len(ok),
            "failed_by_reason": {k: len(v) for k, v in by_reason.items()},
            "failed_doc_ids": {k: v[:200] for k, v in by_reason.items()},
            "expected_fail_docs": expected_fail_observed,
        },
        "gates": {
            "byte_tier": {"n": len(byte_tier), "pass": len(byte_pass),
                          "mismatch_docs": [r["doc"] for r in byte_tier
                                            if r not in byte_pass][:10]},
            "structural_tier": {"n": len(struct_tier), "pass": len(struct_pass)},
        },
        "wedge_events": wedges,
        "meta": meta,
    }

    subs = [r["submit_ns"] for r in rows if "submit_ns" in r]
    comps = [r["completion_ns"] for r in rows if "completion_ns" in r]
    if subs and comps and ok:
        span = (max(comps) - min(subs)) / 1e9
        okc = [r["completion_ns"] for r in ok if "completion_ns" in r]
        lat = sorted((r["completion_ns"] - r["submit_ns"]) / 1e9
                     for r in rows if "completion_ns" in r and "submit_ns" in r)
        out["metrics_emulated_relative_only"] = {
            "batch_span_s": round(span, 1),
            "batch_throughput_docs_s": round(n_offered / span, 3),
            "successful_doc_throughput_docs_s": round(len(ok) / span, 3),
            "ttfr_s": round((min(okc) - min(subs)) / 1e9, 2) if okc else None,
            "batch_position_latency_s_includes_queueing": {
                "p50": round(lat[len(lat) // 2], 1),
                "p90": round(lat[int(len(lat) * 0.9)], 1),
                "p99": round(lat[min(int(len(lat) * 0.99), len(lat) - 1)], 1),
            },
            "send_window_s": (meta or {}).get("send_window_s"),
        }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:5])
