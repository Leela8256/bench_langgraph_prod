"""Gate-50 checker — the SIMPLE correctness gate + bulk-run metrics.

Gates (per arm), all fail-closed via metrics.m0_correctness: census
(manifest identity, unique, zero silent, zero unexpected failures),
structure (per-arm field contract, 384-dim, norm≈1, hash counts; zero-chunk
completions only for allowlisted no-text docs), determinism (blast vs the
sequential pass when out_<arm>_seq exists; None — never a truthy string —
while pending).

Metrics (per arm): warm-window throughput/latency — the first 20
COMPLETIONS are warmup; M1/M2 are computed over completions 21..N.
Latency labeled batch-position (open-loop blast). Resources from samplers.
Cross-arm: chunk-count/char deltas (reported, not gated) + parity fixture.

  python3 gate50/check_gate50.py
"""

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from metrics.m0_correctness import census, determinism, gate_verdict, structure
from metrics.records import load_records

G = ROOT / "gate50"
# Known no-text PDF (12 chars via pypdf too). LG completes it with 0 chunks,
# RR fails it with reason=no_documents — both acceptable, for this doc only.
EXPECTED_EMPTY = {"000164.pdf"}
WARM_N = 20


def load(arm, suffix=""):
    p = G / f"out_{arm}{suffix}" / "per_doc.jsonl"
    if not p.exists():
        return None, None
    rows, meta, _ = load_records(p)
    return rows, meta


def expected_docs(arm, n_expected):
    """Corpus identity so silent drops are named, not just counted: the
    run's own manifest if the driver wrote one, else the same expression
    the drivers use to pick the corpus."""
    m = G / f"out_{arm}" / "manifest.json"
    if m.exists():
        return set(json.loads(m.read_text())["docs"])
    d = ROOT / "datasets" / "govdocs"
    names = sorted(p.name for p in d.glob("*.pdf"))[:n_expected] if d.is_dir() else []
    return set(names) if len(names) == n_expected else None


def gate(rows, arm, seq_rows=None, n_expected=50):
    c = census(rows, n_expected, expected_docs=expected_docs(arm, n_expected),
               expected_empty=EXPECTED_EMPTY)
    s = structure(rows, arm, expected_empty=EXPECTED_EMPTY)
    det = determinism(rows, seq_rows) if seq_rows else None
    return {
        "census": c,
        "structure": s,
        "determinism": det or {"PASS": None,
                               "status": "PENDING (needs sequential pass)"},
        "completed": c["completed"],
        "completed_empty_docs": s["completed_empty"],
        "GATE_census": c["PASS"],
        "GATE_structure": s["PASS"],
        "GATE_determinism": det["PASS"] if det else None,
        "GATE_all": gate_verdict(c, s, det),
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
    rr_seq, _ = load("rr", "_seq")
    lg_seq, _ = load("lg", "_seq")
    for arm, rows, meta, seq in (("rocketride_native_3.3.1", rr_rows, rr_meta, rr_seq),
                                 ("langgraph_docker", lg_rows, lg_meta, lg_seq)):
        report[arm] = {"gate": gate(rows, "rr" if "rocket" in arm else "lg",
                                    seq_rows=seq),
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
