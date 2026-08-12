"""Assemble REPORT_PDF1K.md from runs/pdf1k/*. Run at end of night.

  langgraph-fastapi/.venv/bin/python pdf1k/make_report.py
"""

import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs" / "pdf1k"
OUT = ROOT / "results" / "REPORT_PDF1K.md"


def load_rep(rep_dir: Path):
    d = {"name": rep_dir.name, "dir": rep_dir}
    v = rep_dir / "validation.json"
    if v.exists():
        try:
            d["validation"] = json.loads(v.read_text())
        except Exception:
            d["validation"] = None
    o = rep_dir / "validity_override.json"
    if o.exists():
        d["override"] = json.loads(o.read_text())
    p = rep_dir / "per_doc.jsonl"
    if p.exists():
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        d["meta"] = next((r for r in rows if r.get("kind") == "client_meta"), None)
        d["rows"] = [r for r in rows if r.get("kind") != "client_meta"]
    return d


def sampler_summary(rep_dir: Path):
    f = rep_dir / "container_sampler.jsonl"
    if not f.exists():
        return None
    rss, cpu_s, threads, ts = [], [], [], []
    for line in f.read_text().splitlines():
        try:
            s = json.loads(line)
        except Exception:
            continue
        rss.append(s["rss_mb_sum"])
        cpu_s.append(s["cpu_total_s"])
        threads.append(s["n_threads"])
        ts.append(s["ts"])
    if len(ts) < 5:
        return None
    span = ts[-1] - ts[0]
    cores_used = (cpu_s[-1] - cpu_s[0]) / span if span > 0 else None
    return {
        "samples": len(ts),
        "span_s": round(span, 1),
        "rss_mb_peak": max(rss),
        "rss_mb_median": st.median(rss),
        "threads_peak": max(threads),
        "container_cores_avg": round(cores_used, 2) if cores_used else None,
    }


def fmt_metrics(v):
    m = (v or {}).get("metrics_emulated_relative_only")
    if not m:
        return "  - metrics unavailable\n"
    lat = m.get("batch_position_latency_s", {})
    return (
        f"  - batch span: {m.get('batch_span_s')} s; "
        f"throughput {m.get('batch_throughput_docs_s')} docs/s "
        f"(successful-doc: {m.get('successful_doc_throughput_docs_s')})\n"
        f"  - TTFR: {m.get('ttfr_s')} s; batch-position latency p50/p90/p99: "
        f"{lat.get('p50')}/{lat.get('p90')}/{lat.get('p99')} s (includes queueing)\n"
        f"  - send window: {m.get('send_window_s')} s\n"
    )


def main():
    prov = json.loads((RUNS / "provenance.json").read_text())
    frozen = {}
    fz = RUNS / "frozen_params.json"
    if fz.exists():
        frozen = json.loads(fz.read_text())
    reps = [load_rep(d) for d in sorted(RUNS.iterdir())
            if d.is_dir() and (d / "per_doc.jsonl").exists() and d.name != "probe"]

    S = []
    S.append("# PDF-1K concurrency benchmark — RocketRide vs LangGraph\n")
    S.append("> **EMULATED HARDWARE**: linux/amd64 images on an arm64 Apple "
             "M5 Pro host. Every timing below is relative-comparison-only; "
             "absolute figures are not portable. No native numbers exist yet.\n")
    S.append("Question: out-of-the-box defaults under a 1000-doc open-loop "
             "burst. NOT a scheduler-tuning comparison — no `threads=`, no "
             "`max_concurrency`, no `chunk_size` set anywhere.\n")

    S.append("## Setup (provenance: `runs/pdf1k/provenance.json`)\n")
    S.append(f"- containers: {prov['containers']['cpus']} CPU / "
             f"{prov['containers']['memory_gb']} GB each (preregistered); host "
             f"{prov['host']['model']}, {prov['host']['cores']} cores / "
             f"{prov['host']['ram_gb']} GB; images run emulated")
    S.append(f"- corpus: 1000 Govdocs1 PDFs, manifest sha "
             f"`{prov['corpus_manifest_sha256'][:16]}…`; ground truth 999/1000 "
             f"extractable (offline pypdf {prov['lg_versions']['pypdf']})")
    S.append(f"- model: multi-qa-MiniLM-L6-cos-v1 @ HF rev "
             f"`{prov.get('hf_model_revision','?')[:12]}`; "
             f"sentence-transformers {prov['lg_versions']['sentence-transformers']}, "
             f"torch {prov['lg_versions']['torch']}")
    S.append(f"- LG dispatch (provenance only): {prov['lg_dispatch_note']}")
    S.append(f"- frozen params: {json.dumps(frozen) if frozen else 'see frozen_params.json'}\n")

    S.append("## Reps\n")
    for r in reps:
        v = r.get("validation") or {}
        ov = r.get("override")
        valid = ov["valid"] if ov else v.get("valid")
        S.append(f"### {r['name']} — {'VALID' if valid else 'INVALID'}"
                 f"{' (' + ov.get('reason', '') + ')' if ov else ''}\n")
        S.append(f"  - records {v.get('n_records')}/{v.get('n_expected')}, "
                 f"ok {v.get('n_ok')}; gates: returned={v.get('all_returned')} "
                 f"unique={v.get('ids_unique')} dims={v.get('dims_ok')} "
                 f"finite={v.get('finite_ok')} norms={v.get('norms_ok')} "
                 f"gt_exact={v.get('gt_exact', v.get('consistent_with_baseline'))}")
        S.append(fmt_metrics(v))
        samp = sampler_summary(r["dir"])
        if samp:
            S.append(f"  - sampler: peak RSS {samp['rss_mb_peak']} MB, "
                     f"avg cores {samp['container_cores_avg']}, "
                     f"peak threads {samp['threads_peak']} "
                     f"({samp['samples']} samples @100ms)\n")
        rows = r.get("rows") or []
        fails = [x for x in rows if not x.get("ok")]
        if fails:
            kinds = {}
            for f in fails:
                k = (f.get("error") or "?")[:40]
                kinds[k] = kinds.get(k, 0) + 1
            S.append(f"  - failures ({len(fails)}): {json.dumps(kinds)}\n")

    S.append("\n_Assembled by pdf1k/make_report.py; narrative sections are "
             "added by hand on top of this skeleton._\n")
    OUT.write_text("\n".join(S))
    print(f"wrote {OUT} ({len(reps)} reps)")


if __name__ == "__main__":
    main()
