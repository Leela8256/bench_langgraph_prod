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


def efficiency(successful_docs: int, cpu_seconds: float) -> Dict[str, Any]:
    if not cpu_seconds or successful_docs is None:
        return {"error": "missing inputs"}
    return {
        "docs_per_cpu_second": round(successful_docs / cpu_seconds, 4),
        "cpu_seconds_per_doc": round(cpu_seconds / successful_docs, 2)
        if successful_docs else None,
    }
