"""The video benchmark report — gates first, numbers second, fail-closed.

Every metric is re-derivable from the raw records forever: download a run
from S3 and get the same numbers on any machine.

  python3 bench/report.py <run_dir>                        one run
  python3 bench/report.py <rep1> <rep2> [rep3...]          reps of ONE arm
                                                           (adds determinism)
  python3 bench/report.py --arms <rrA[,rrB...]> <lgA[,lgB...]>
                                                           cross-arm gates too

A run_dir needs per_doc.jsonl; engine_cgroup.csv, manifest.json and
progress.jsonl are used when present. Exit non-zero if any HARD gate
fails (SKIP/WARN are surfaced, not fatal — but a SKIP on a gate the claim
depends on means the claim cannot be made).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from metrics import v0_gates as v0
from metrics import v_metrics as vm


def load_run(d):
    d = Path(d)
    recs, meta = [], {}
    for line in open(d / "per_doc.jsonl"):
        r = json.loads(line)
        if r.get("kind") == "shot_meta":
            meta = r
        else:
            recs.append(r)
    manifest = {}
    if (d / "manifest.json").exists():
        manifest = json.loads((d / "manifest.json").read_text())
    sampler = []
    for f in sorted(d.glob("*cgroup*.csv")):
        for line in open(f):
            p = line.strip().split(",")
            if p and p[0].isdigit():
                sampler.append(tuple(int(x) for x in p if x.lstrip("-").isdigit()))
    progress = []
    if (d / "progress.jsonl").exists():
        progress = [json.loads(l) for l in open(d / "progress.jsonl")]
    return {"dir": str(d), "records": recs, "meta": meta,
            "manifest": manifest, "sampler": sampler, "progress": progress}


def gate_line(g):
    mark = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "warn", "SKIP": "SKIP"}[g["status"]]
    return f"  [{mark}] {g['gate']:18s} {g['detail']}"


def run_gates(run):
    recs, man = run["records"], run["manifest"].get("docs") or \
        [r["doc"] for r in run["records"]]
    return [v0.census(recs, man), v0.structure(recs),
            v0.frame_law(recs), v0.self_duplication(recs),
            v0.corpus_pin(recs, run["manifest"])]


def report_one(run):
    print(f"\n== {run['dir']}  arm={run['meta'].get('arm', '?')} "
          f"mode={run['meta'].get('mode', '?')}")
    gates = run_gates(run)
    for g in gates:
        print(gate_line(g))
    hard_fail = any(g["status"] == "FAIL" for g in gates)
    if hard_fail:
        print("  GATES FAILED — numbers below are diagnostic only, not quotable")
    v1 = vm.v1_throughput(run["records"], run["meta"])
    v2 = vm.v2_latency(run["records"], run["meta"], run["progress"])
    cpu = vm.cpu_from_sampler(run["sampler"])
    v3 = vm.v3_efficiency(run["records"], run["meta"], cpu)
    v4 = vm.v4_resources(run["records"], run["meta"], cpu)
    v5 = vm.v5_cost(v1)
    for name, block in (("V1 throughput", v1), ("V2 latency", v2),
                        ("V3 efficiency", v3), ("V4 resources", v4),
                        ("V5 cost", v5)):
        print(f"  {name}: {json.dumps(block)}")
    cov = vm.coverage(
        {"V1": v1, "V2": v2, "V3": v3, "V5": v5},
        exemptions=("frames" if any(g["gate"] == "frame_law" and
                                    g["status"] == "SKIP" for g in gates) else "",
                    "threads_activated",          # sampler-version dependent
                    "time_to_first_result",       # mode dependent
                    "cpu_s_per_frame", "cpu_s_per_detection",
                    "peak_mem", "cold_to_ready"))
    print(gate_line(cov))
    gates.append(cov)
    print(f"  envelope: {run['meta'].get('envelope', 'NOT RECORDED')}")
    run["v1"] = v1
    return gates


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    all_gates = []

    if args[0] == "--arms":
        rr_dirs, lg_dirs = args[1].split(","), args[2].split(",")
        rr_runs = [load_run(d) for d in rr_dirs]
        lg_runs = [load_run(d) for d in lg_dirs]
        for run in rr_runs + lg_runs:
            all_gates += report_one(run)
        for name, runs in (("rocketride", rr_runs), ("langgraph", lg_runs)):
            g = v0.determinism([{r["doc"]: r for r in x["records"]} for x in runs])
            print(f"\n== determinism [{name}]")
            print(gate_line(g))
            all_gates.append(g)
        print("\n== cross-arm (rep1 vs rep1)")
        a = {r["doc"]: r for r in rr_runs[0]["records"]}
        b = {r["doc"]: r for r in lg_runs[0]["records"]}
        for g in ([v0.input_identity(a, b)] + v0.cross_arm(a, b, "rr", "lg")
                  + [v0.chunk_parity_tight(a, b)]):
            print(gate_line(g))
            all_gates.append(g)
        print(f"  workload_ratio_rr_over_lg: {v0.workload_ratio(a, b)}")
    else:
        runs = [load_run(d) for d in args]
        for run in runs:
            all_gates += report_one(run)
        if len(runs) > 1:
            g = v0.determinism([{r["doc"]: r for r in x["records"]} for x in runs])
            print("\n== determinism (across given runs)")
            print(gate_line(g))
            all_gates.append(g)
            by_mode = {x["meta"].get("mode"): x.get("v1", {}) for x in runs}
            conc = next((int(m[1:]) for m in by_mode if m and m.startswith("c")), None)
            cm = vm.cross_mode(by_mode, conc)
            if cm:
                print(f"  cross-mode: {json.dumps(cm)}")
        else:
            print("\n  NOTE: single run — determinism unproven; not a "
                  "benchmark result (sizing evidence only)")

    fails = [g for g in all_gates if g["status"] == "FAIL"]
    skips = [g for g in all_gates if g["status"] == "SKIP"]
    print(f"\n== verdict: {'FAIL' if fails else 'PASS'}"
          f" ({len(fails)} hard failures, {len(skips)} skipped gates)")
    if fails:
        for g in fails:
            print(f"  FAILED: {g['gate']}: {g['detail'][:120]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
