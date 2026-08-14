"""Prove the consolidated metrics library reproduces gate-50's numbers.

Recomputes M0/M1/M2/M7 from the raw gate50 records via metrics/* and
compares against the values in gate50/GATE50_REPORT.json (computed by the
old checker). Any drift means the consolidation changed a formula.

  python3 -m metrics.selfcheck
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics.m0_correctness import census, structure  # noqa: E402
from metrics.m1_m2_perf import latency, throughput  # noqa: E402
from metrics.m7_resources import container_resources, native_resources  # noqa: E402
from metrics.records import load_records  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OLD = json.loads((ROOT / "gate50" / "GATE50_REPORT.json").read_text())


def close(a, b, tol=0.02):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


def main() -> int:
    failures = []
    for arm, key, sampler_fn, sampler_file in (
        ("rr", "rocketride_native_3.3.1", native_resources, "host_sampler.jsonl"),
        ("lg", "langgraph_docker", container_resources, "container_sampler.jsonl"),
    ):
        rows, meta, _ = load_records(ROOT / "gate50" / f"out_{arm}" / "per_doc.jsonl")
        old = OLD[key]

        c = census(rows, 50, expected_empty={"000164.pdf"})
        if c["completed"] != old["gate"]["completed"]:
            failures.append(f"{arm} census.completed {c['completed']} != {old['gate']['completed']}")
        if not c["PASS"] == old["gate"]["GATE_census"]:
            failures.append(f"{arm} census PASS mismatch")

        s = structure(rows, arm)
        if s["PASS"] != old["gate"]["GATE_structure"]:
            failures.append(f"{arm} structure PASS mismatch")

        t = throughput(rows, warm_n=20)
        old_t = old["metrics"]["M1_throughput_docs_s"]
        if not close(t["docs_per_s"], old_t):
            failures.append(f"{arm} M1 {t['docs_per_s']} != {old_t}")

        l = latency(rows, warm_n=20, mode="open-loop-blast")
        old_l = old["metrics"]["M2_latency_batch_position_s"]
        for p in ("p50", "p90", "p95", "p99"):
            if not close(l[p], old_l[p]):
                failures.append(f"{arm} M2 {p} {l[p]} != {old_l[p]}")

        r = sampler_fn(ROOT / "gate50" / f"out_{arm}" / sampler_file)
        old_r = old.get("resources") or {}
        if r and old_r:
            if not close(r["rss_mb"]["peak"], old_r["rss_mb"]["peak"]):
                failures.append(f"{arm} M7 rss peak {r['rss_mb']['peak']} != {old_r['rss_mb']['peak']}")
            new_thr = r["threads"]["peak"]
            old_thr = old_r["threads"]["peak"]
            if not close(new_thr, old_thr, tol=0.05):
                failures.append(f"{arm} M7 threads peak {new_thr} != {old_thr}")

        print(f"[{arm}] census={c['PASS']} structure={s['PASS']} "
              f"M1={t['docs_per_s']} M2.p50={l['p50']} "
              f"cores={r.get('effective_cores') if r else '?'}")

    if failures:
        print("\nSELF-CHECK FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nSELF-CHECK PASS: consolidated metrics reproduce gate-50 exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
