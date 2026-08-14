"""M7 — resource footprint + efficiency, from sampler JSONL streams."""

import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, Optional


def _load(path) -> list:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def container_resources(sampler_path) -> Optional[Dict[str, Any]]:
    """From pdf1k/proc_sampler.py output (cpu_total_s cumulative)."""
    lines = _load(sampler_path)
    if len(lines) < 3:
        return None
    a, b = lines[0], lines[-1]
    span = b["ts"] - a["ts"]
    rss = [l["rss_mb_sum"] for l in lines]
    thr = [l["n_threads"] for l in lines]
    cpu_s = b["cpu_total_s"] - a["cpu_total_s"]
    return {
        "samples": len(lines), "span_s": round(span, 1),
        "cpu_seconds": round(cpu_s, 1),
        "effective_cores": round(cpu_s / span, 2) if span > 0 else None,
        "rss_mb": {"peak": max(rss), "median": st.median(rss),
                   "start": rss[0], "end": rss[-1],
                   "growth": round(rss[-1] - rss[0], 1)},
        "threads": {"peak": max(thr), "median": st.median(thr)},
    }


def native_resources(sampler_path) -> Optional[Dict[str, Any]]:
    """From gate50/host_sampler_native.py output (%CPU instantaneous)."""
    lines = _load(sampler_path)
    if len(lines) < 3:
        return None
    span = lines[-1]["ts"] - lines[0]["ts"]
    rss = [l["rss_mb_sum"] for l in lines]
    thr = [l["threads_sum"] for l in lines]
    cpu = [l["cpu_pct_sum"] for l in lines]
    mean_cores = st.mean(cpu) / 100
    return {
        "samples": len(lines), "span_s": round(span, 1),
        "cpu_seconds": round(mean_cores * span, 1),
        "effective_cores": round(mean_cores, 2),
        "peak_cores": round(max(cpu) / 100, 2),
        "rss_mb": {"peak": max(rss), "median": st.median(rss),
                   "start": rss[0], "end": rss[-1],
                   "growth": round(rss[-1] - rss[0], 1)},
        "threads": {"peak": max(thr), "median": st.median(thr)},
    }


def window(sampler_path, t0_ns: Optional[int], t1_ns: Optional[int],
           mono_offset_ns: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Re-slice a sampler stream to EXACTLY the throughput window.

    Resource cost and throughput must cover the same span, or cost-per-work is
    a ratio of two different things -- setup and warm-up CPU otherwise land in
    the window and inflate it. Anchors on the last sample at or before t0 (the
    cumulative counter's value entering the window), not the first sample
    after it.

    Sampler ts is wall-clock epoch seconds; per-doc timestamps are monotonic
    ns. mono_offset_ns converts: epoch_ns = mono_ns + mono_offset_ns, recorded
    by the driver. Without it, returns None rather than guess.
    """
    if t0_ns is None or t1_ns is None or mono_offset_ns is None:
        return None
    lines = _load(sampler_path)
    if len(lines) < 3:
        return None
    t0 = (t0_ns + mono_offset_ns) / 1e9
    t1 = (t1_ns + mono_offset_ns) / 1e9
    before = [l for l in lines if l["ts"] <= t0]
    inside = [l for l in lines if t0 < l["ts"] <= t1]
    if not inside:
        return None
    a = before[-1] if before else inside[0]
    b = inside[-1]
    span = b["ts"] - a["ts"]
    if span <= 0:
        return None
    rss = [l["rss_mb_sum"] for l in inside]
    thr = [l["n_threads"] for l in inside]
    cpu_s = b["cpu_total_s"] - a["cpu_total_s"]
    return {
        "samples_in_window": len(inside), "span_s": round(span, 2),
        "cpu_seconds": round(cpu_s, 2),
        "effective_cores": round(cpu_s / span, 3),
        "rss_mb": {"peak": max(rss), "median": st.median(rss),
                   "start": rss[0], "end": rss[-1],
                   "growth": round(rss[-1] - rss[0], 1)},
        "threads": {"peak": max(thr), "median": st.median(thr),
                    "note": "observation only — threads include torch, "
                            "tokenizers, GC and HTTP runtime; NOT proof of "
                            "parallel document execution"},
    }


def efficiency(successful_docs: Optional[int], cpu_seconds: Optional[float],
               successful_chunks: Optional[int] = None,
               wall_seconds: Optional[float] = None,
               available_cpus: Optional[int] = None) -> Dict[str, Any]:
    """Work-normalised cost. Per-chunk pairs with chunks_per_s: it is the
    honest denominator when two frameworks split the same PDFs differently.

    cpu_utilization answers what throughput alone hides -- 20 docs/s at 80%
    CPU and 19 docs/s at 40% are very different results. It is bounded at 1.0
    by construction; above that means cost was attributed outside the window
    (a real bug this suite has already hit), so it is flagged, never clamped.
    """
    if not cpu_seconds or successful_docs is None:
        return {"error": "missing inputs"}
    out: Dict[str, Any] = {
        "docs_per_cpu_second": round(successful_docs / cpu_seconds, 4),
        "cpu_seconds_per_doc": round(cpu_seconds / successful_docs, 3)
        if successful_docs else None,
    }
    if successful_chunks:
        out["chunks_per_cpu_second"] = round(successful_chunks / cpu_seconds, 4)
        out["cpu_seconds_per_chunk"] = round(cpu_seconds / successful_chunks, 4)
    else:
        out["cpu_seconds_per_chunk"] = None
    if wall_seconds and available_cpus:
        util = cpu_seconds / (wall_seconds * available_cpus)
        out["cpu_utilization"] = round(util, 4)
        out["available_cpus"] = available_cpus
        out["cpu_utilization_valid"] = util <= 1.0
        if util > 1.0:
            out["cpu_utilization_error"] = (
                "utilization > 1.0 — CPU attributed outside the measured "
                "window or outside this container; the run is INVALID")
    return out
