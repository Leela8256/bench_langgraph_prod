"""Smoke report — derives M0/M1/M2/M7 from raw records using metrics/ ONLY.

No metric is computed here; every number comes from the canonical modules, so
the smoke test also proves the metrics library runs on the box.

  python3 aws_run/box/smoke_report.py <run_dir> [<run_dir_2>]
        (run_dir_2 enables the M0 determinism check)
Run from the repo root.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from metrics.m0_correctness import census, determinism, gate_verdict, structure
from metrics.m1_m2_perf import latency, throughput, ttfr
from metrics.m7_resources import container_resources, efficiency
from metrics.records import load_records

ARM = "lg"
# 10 docs cannot support the 20/25-completion warm-start rule (metrics/README
# M1). warm_n=0 means the window includes cold effects -> smoke signal only.
WARM_N = 0
# Same allowlist gate50/check_gate50.py uses: a genuinely no-text PDF (12
# chars via pypdf too). Only reachable if it lands in the first N govdocs.
EXPECTED_EMPTY = {"000164.pdf"}


def main():
    run = Path(sys.argv[1])
    rows, meta, _ = load_records(run / "per_doc.jsonl")
    manifest = json.loads((run / "manifest.json").read_text())
    expected = set(manifest["docs"])

    rep = {"run_dir": str(run), "meta": meta}
    rep["census"] = census(rows, offered=manifest["n"], expected_docs=expected,
                           expected_empty=EXPECTED_EMPTY)
    rep["structure"] = structure(rows, arm=ARM, expected_empty=EXPECTED_EMPTY)

    det = None
    if len(sys.argv) > 2:
        rows_b, _, _ = load_records(Path(sys.argv[2]) / "per_doc.jsonl")
        det = determinism(rows, rows_b)
        rep["determinism"] = det

    rep["m0_PASS"] = gate_verdict(rep["census"], rep["structure"], det) if det \
        else gate_verdict(rep["census"], rep["structure"])
    rep["m0_note"] = ("determinism included" if det else
                      "determinism NOT run — single pass, gate incomplete")

    rep["m1_throughput"] = throughput(rows, warm_n=WARM_N)
    rep["m2_latency"] = latency(rows, warm_n=WARM_N, mode="closed-loop")
    rep["ttfr_s"] = ttfr(rows)
    rep["warm_start_note"] = (
        f"warm_n={WARM_N}: 10 docs cannot support the 20/25 rule. Throughput "
        "includes cold-cache effects — smoke signal, NOT a publishable rate."
    )

    sampler = run / "sampler.jsonl"
    if sampler.exists():
        res = container_resources(sampler)
        rep["m7_resources"] = res
        head = json.loads(sampler.read_text().splitlines()[0])
        rep["m7_sampler_source"] = {
            "cpu": head.get("cpu_source"), "rss": head.get("rss_source"),
        }
        if res:
            ok = rep["m1_throughput"].get("successful_in_window")
            rep["m7_efficiency"] = efficiency(ok, res["cpu_seconds"])
            cores = res.get("effective_cores")
            # Teammate's invariant, adopted: >cpus means cost was attributed
            # outside the container/span. This is what caught the /proc/stat bug.
            rep["m7_sanity"] = {
                "effective_cores": cores,
                "note": "must be <= container cpu limit; higher = mis-attribution",
            }
    else:
        rep["m7_resources"] = None

    (run / "SMOKE_REPORT.json").write_text(json.dumps(rep, indent=1, default=str))

    print(json.dumps(rep, indent=1, default=str))
    print("\n" + "=" * 62)
    print(f"M0 correctness gate : {'PASS' if rep['m0_PASS'] else 'FAIL'}"
          f"   ({rep['m0_note']})")
    t, l = rep["m1_throughput"], rep["m2_latency"]
    print(f"M1 throughput       : {t.get('docs_per_s')} docs/s "
          f"({t.get('successful_in_window')} ok / {t.get('window_span_s')} s)")
    print(f"M2 latency (closed) : p50={l.get('p50')}s p95={l.get('p95')}s "
          f"max={l.get('max')}s")
    if rep.get("m7_resources"):
        r = rep["m7_resources"]
        print(f"M7 resources        : {r['cpu_seconds']} cpu-s, "
              f"{r['effective_cores']} eff cores, "
              f"peak RSS {r['rss_mb']['peak']} MB, peak threads "
              f"{r['threads']['peak']}")
        print(f"M7 efficiency       : {rep.get('m7_efficiency')}")
    print("=" * 62)
    print(f"written: {run / 'SMOKE_REPORT.json'}")
    return 0 if rep["m0_PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
