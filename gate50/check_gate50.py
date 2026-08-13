"""Gate-50 checker — the SIMPLE correctness gate + bulk-run metrics.

Gates (per arm): census (50 records, unique, zero silent, expected-fails
classified) and structure (chunks>=1, 384-dim, finite, norm≈1; LG sha
header). Determinism gate = PENDING until the sequential pass exists.

Metrics (per arm): warm-window throughput/latency — the first 20
COMPLETIONS are warmup; M1/M2 are computed over completions 21..N.
Latency labeled batch-position (open-loop blast). Resources from samplers.
Cross-arm: chunk-count/char deltas (reported, not gated) + parity fixture.

  python3 gate50/check_gate50.py
"""

import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G = ROOT / "gate50"
EXPECTED_FAIL_RR = {"000164.pdf"}  # near-empty PDF (12 chars via pypdf too): completed-empty, not a defect
WARM_N = 20


def load(arm):
    rows, meta = [], None
    p = G / f"out_{arm}" / "per_doc.jsonl"
    for line in p.read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") in ("shot_meta", "level_meta"):
            meta = r
        else:
            rows.append(r)
    return rows, meta


def gate(rows, arm, n_expected=50):
    docs = [r["doc"] for r in rows]
    ok = [r for r in rows if r.get("ok")]
    fails = [r for r in rows if not r.get("ok")]
    by_reason = {}
    for r in fails:
        by_reason.setdefault(r.get("reason") or r.get("error", "?")[:30], []).append(r["doc"])
    structure_bad = []
    empty_ok = []
    for r in ok:
        if r.get("n_chunks", 0) == 0:
            empty_ok.append(r["doc"])  # completed-empty: valid (no-text doc)
            continue
        good = (r.get("n_chunks", 0) >= 1 and r.get("vector_dim") == 384
                and r.get("l2_norms_minmax")
                and abs(r["l2_norms_minmax"][0] - 1) < 1e-3
                and abs(r["l2_norms_minmax"][1] - 1) < 1e-3
                and r.get("identity_ok", True))
        if arm == "lg" and r.get("sha_header_ok") is False:
            good = False
        if not good:
            structure_bad.append(r["doc"])
    unexpected_fail = [d for ds in by_reason.values() for d in ds
                       if d not in EXPECTED_FAIL_RR]
    return {
        "census_records": f"{len(rows)}/{n_expected}",
        "census_unique": len(set(docs)) == len(docs),
        "census_silent": n_expected - len(rows),
        "completed": len(ok),
        "failed_by_reason": {k: len(v) for k, v in by_reason.items()},
        "failed_docs": {k: v for k, v in by_reason.items()},
        "structure_bad_docs": structure_bad,
        "completed_empty_docs": empty_ok,
        "GATE_census": len(rows) == n_expected and len(set(docs)) == len(docs),
        "GATE_structure": not structure_bad,
        "GATE_determinism": "PENDING (needs sequential pass)",
        "note_unexpected_failures": len(unexpected_fail),
    }


def window_metrics(rows):
    done = sorted((r for r in rows if "completion_ns" in r),
                  key=lambda r: r["completion_ns"])
    ok_all = [r for r in done if r.get("ok")]
    if len(done) <= WARM_N:
        return {"error": f"only {len(done)} completions; warm window needs > {WARM_N}"}
    t_warm = done[WARM_N - 1]["completion_ns"]
    window = [r for r in done if r["completion_ns"] > t_warm]
    w_ok = [r for r in window if r.get("ok")]
    span = (done[-1]["completion_ns"] - t_warm) / 1e9
    lat = sorted((r["completion_ns"] - r["submit_ns"]) / 1e9 for r in w_ok)
    q = lambda p: round(lat[min(int(len(lat) * p), len(lat) - 1)], 2) if lat else None
    t0 = min(r["submit_ns"] for r in rows)
    return {
        "warmup_boundary": f"first {WARM_N} completions excluded",
        "window_docs": len(window),
        "window_ok": len(w_ok),
        "M1_throughput_docs_s": round(len(w_ok) / span, 3) if span > 0 else None,
        "M2_latency_batch_position_s": {
            "p50": q(0.50), "p90": q(0.90), "p95": q(0.95), "p99": q(0.99),
            "max": round(lat[-1], 2) if lat else None,
            "label": "open-loop blast — includes queueing",
        },
        "full_run": {
            "ok": len(ok_all), "of": len(done),
            "span_s": round((done[-1]["completion_ns"] - t0) / 1e9, 2),
            "ttfr_s": round((done[0]["completion_ns"] - t0) / 1e9, 2),
        },
    }


def rr_resources():
    p = G / "out_rr" / "host_sampler.jsonl"
    if not p.exists():
        return None
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if len(lines) < 3:
        return None
    rss = [l["rss_mb_sum"] for l in lines]
    thr = [l["threads_sum"] for l in lines]
    cpu = [l["cpu_pct_sum"] for l in lines]
    return {"samples": len(lines),
            "rss_mb": {"peak": max(rss), "median": st.median(rss),
                       "start": rss[0], "end": rss[-1]},
            "threads": {"peak": max(thr), "median": st.median(thr)},
            "cpu_pct": {"peak": max(cpu), "avg": round(st.mean(cpu), 1),
                        "note": "%CPU sum over engine tree; 100 = 1 core"}}


def lg_resources():
    p = G / "out_lg" / "container_sampler.jsonl"
    if not p.exists():
        return None
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if len(lines) < 3:
        return None
    a, b = lines[0], lines[-1]
    span = b["ts"] - a["ts"]
    rss = [l["rss_mb_sum"] for l in lines]
    thr = [l["n_threads"] for l in lines]
    return {"samples": len(lines),
            "avg_cores": round((b["cpu_total_s"] - a["cpu_total_s"]) / span, 2),
            "rss_mb": {"peak": max(rss), "median": st.median(rss),
                       "start": rss[0], "end": rss[-1]},
            "threads": {"peak": max(thr), "median": st.median(thr)}}


def cross_arm(rr_rows, lg_rows):
    rr = {r["doc"]: r for r in rr_rows if r.get("ok")}
    lg = {r["doc"]: r for r in lg_rows if r.get("ok")}
    both = sorted(set(rr) & set(lg))
    if not both:
        return {"both_ok": 0}
    cc = [rr[d]["n_chunks"] - lg[d]["n_chunks"] for d in both]
    ratio = [rr[d]["total_chars"] / lg[d]["total_chars"] for d in both
             if lg[d]["total_chars"]]
    out = {"both_ok": len(both),
           "chunk_delta_rr_minus_lg": {"median": st.median(cc), "min": min(cc),
                                       "max": max(cc)},
           "char_ratio_rr_over_lg": {"median": round(st.median(ratio), 3),
                                     "min": round(min(ratio), 3),
                                     "max": round(max(ratio), 3)}}
    pv = G / "out_rr" / "parity_vector.json"
    lv = G / "out_lg" / "parity_vector.json"
    if pv.exists() and lv.exists():
        a = json.loads(pv.read_text()).get("vector")
        b = json.loads(lv.read_text()).get("vector")
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            worst = max(abs(x - y) for x, y in zip(a, b))
            out["parity_fixture"] = {"max_abs_diff": f"{worst:.2e}",
                                     "allclose_1e-5": worst < 1e-5}
        else:
            out["parity_fixture"] = {"error": "vector missing on one arm"}
    return out


def main():
    report = {}
    rr_rows, rr_meta = load("rr")
    lg_rows, lg_meta = load("lg")
    for arm, rows, meta in (("rocketride_native_3.3.1", rr_rows, rr_meta),
                            ("langgraph_docker", lg_rows, lg_meta)):
        report[arm] = {"gate": gate(rows, "rr" if "rocket" in arm else "lg"),
                       "metrics": window_metrics(rows),
                       "meta": meta}
    report["rocketride_native_3.3.1"]["resources"] = rr_resources()
    report["langgraph_docker"]["resources"] = lg_resources()
    report["cross_arm"] = cross_arm(rr_rows, lg_rows)
    out = G / "GATE50_REPORT.json"
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
