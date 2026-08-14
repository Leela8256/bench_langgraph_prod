"""M1 throughput + M2 latency. See README for formulas and labeling rules."""

from typing import Any, Dict, List, Optional

from metrics.records import by_completion, latency_s, ok_records


def perf_window(rows: List[Dict], warm_n: int = 0) -> Dict[str, Any]:
    """Slice a run into (warmup, window) by COMPLETION order.

    warm_n=0 -> whole run is the window. warm_n=20/25 -> the first N
    completions are excluded from measurement; the window spans from the
    Nth completion to the last.
    """
    done = by_completion(rows)
    if len(done) <= warm_n:
        return {"error": f"{len(done)} completions <= warm_n={warm_n}"}
    if warm_n:
        boundary_ns = done[warm_n - 1]["completion_ns"]
        window = [r for r in done if r["completion_ns"] > boundary_ns]
        span_s = (done[-1]["completion_ns"] - boundary_ns) / 1e9
    else:
        boundary_ns = None
        window = done
        t0 = min(r["submit_ns"] for r in rows if "submit_ns" in r)
        span_s = (done[-1]["completion_ns"] - t0) / 1e9
    return {"window": window, "span_s": span_s, "warm_n": warm_n,
            "boundary_ns": boundary_ns}


def throughput(rows: List[Dict], warm_n: int = 0) -> Dict[str, Any]:
    w = perf_window(rows, warm_n)
    if "error" in w:
        return w
    ok = ok_records(w["window"])
    return {
        "successful_in_window": len(ok),
        "window_docs": len(w["window"]),
        "window_span_s": round(w["span_s"], 3),
        "docs_per_s": round(len(ok) / w["span_s"], 4) if w["span_s"] > 0 else None,
        "warm_n": warm_n,
    }


def latency(rows: List[Dict], warm_n: int = 0,
            mode: str = "closed-loop") -> Dict[str, Any]:
    """mode is a LABEL: 'closed-loop' => true service latency;
    'open-loop-blast' => batch-position latency (includes queue wait)."""
    w = perf_window(rows, warm_n)
    if "error" in w:
        return w
    lats = sorted(latency_s(r) for r in ok_records(w["window"]))
    if not lats:
        return {"error": "no successful docs in window"}
    q = lambda p: round(lats[min(int(len(lats) * p), len(lats) - 1)], 3)
    return {
        "n": len(lats),
        "p50": q(0.50), "p90": q(0.90), "p95": q(0.95), "p99": q(0.99),
        "max": round(lats[-1], 3),
        "mean": round(sum(lats) / len(lats), 3),
        "label": ("true service latency" if mode == "closed-loop"
                  else "batch-position latency — includes queue wait"),
        "mode": mode, "warm_n": warm_n,
    }


def ttfr(rows: List[Dict]) -> Optional[float]:
    """Time to first result: first completion - first submission."""
    done = by_completion(ok_records(rows))
    if not done:
        return None
    t0 = min(r["submit_ns"] for r in rows if "submit_ns" in r)
    return round((done[0]["completion_ns"] - t0) / 1e9, 3)
