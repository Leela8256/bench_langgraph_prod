"""Blast-mode report — per-rep metrics + across-rep stability + provenance.

Implements the adopted changes: chunks/s beside docs/s, CPU-s/chunk,
effective cores, CPU utilization, offered vs configured vs ACHIEVED
concurrency, threads as observation only, n>=3 reps with CV, and a resource
window sliced to exactly the throughput window.

Blast latency is labelled BATCH-POSITION LATENCY throughout: it includes queue
wait, because the whole backlog is submitted at t=0. It must never be compared
with closed-loop service latency -- they answer different questions.

  python3 bench/report.py <run_dir>
Expects <run_dir>/<arm>/rep<N>/per_doc.jsonl. Run from the repo root.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # aws_bench/
sys.path.insert(0, str(ROOT))

from metrics.m0_correctness import (census, cross_arm, determinism,
                                    gate_verdict, structure)
from metrics.m1_m2_perf import achieved_concurrency, latency, throughput, ttfr
from metrics.m7_resources import container_resources, efficiency, window
from metrics.provenance import check as prov_check
from metrics.records import load_records, ok_records
from metrics.stability import across_reps

WARM_N = 0        # blast submits everything at t=0; a warm-window slice of a
                  # single batch is not meaningful. Warm-up is excluded by the
                  # driver's uncounted warm-up doc instead.
EXPECTED_EMPTY = {"000164.pdf"}
ARMS = {"lg": "langgraph", "rr": "rocketride"}


def rep_report(d: Path, arm: str, cpus: int) -> dict:
    f = d / "per_doc.jsonl"
    if not f.exists():
        return {"error": f"missing {f}"}
    rows, meta, _ = load_records(f)
    manifest = json.loads((d / "manifest.json").read_text())
    meta = meta or {}

    rep = {"rep_dir": d.name, "meta": meta}
    rep["census"] = census(rows, offered=manifest["n"],
                           expected_docs=set(manifest["docs"]),
                           expected_empty=EXPECTED_EMPTY)
    rep["structure"] = structure(rows, arm=arm, expected_empty=EXPECTED_EMPTY)
    rep["m0_PASS_partial"] = gate_verdict(rep["census"], rep["structure"])

    t = throughput(rows, warm_n=WARM_N)
    rep["m1_throughput"] = t
    lat = latency(rows, warm_n=WARM_N, mode="open-loop-blast")
    lat["label"] = "BATCH-POSITION LATENCY — includes queue wait"
    rep["m2_latency"] = lat
    rep["ttfr_s"] = ttfr(rows)

    ac = achieved_concurrency(rows, warm_n=WARM_N)
    rep["concurrency"] = {
        "offered": meta.get("offered_concurrency"),
        "configured_note": meta.get("configured_concurrency_note"),
        "achieved_peak": ac.get("peak_achieved"),
        "achieved_mean_time_weighted": ac.get("mean_achieved_time_weighted"),
        "note": "offered != configured != achieved; never treat as equal",
    }

    sampler = d / "sampler.jsonl"
    if sampler.exists():
        # Sliced to the SAME window as throughput. Falls back to whole-stream
        # only if the driver did not record the clock offset, and says so.
        w = window(sampler, t.get("window_t0_ns"), t.get("window_t1_ns"),
                   meta.get("mono_offset_ns"))
        if w:
            rep["m7_resources"] = w
            rep["m7_window"] = "sliced to throughput window"
        else:
            rep["m7_resources"] = container_resources(sampler)
            rep["m7_window"] = ("WHOLE STREAM — not window-matched; "
                                "cost-per-work may include setup")
        res = rep.get("m7_resources") or {}
        if res:
            rep["m7_efficiency"] = efficiency(
                t.get("successful_in_window"), res.get("cpu_seconds"),
                t.get("successful_chunks"), t.get("window_span_s"), cpus)
    tika = d / "sampler_tika.jsonl"
    if tika.exists():
        rep["m7_resources_tika_sidecar"] = container_resources(tika)
        rep["m7_note"] = ("LG parses in the tika sidecar; its CPU is NOT in "
                          "the langgraph cgroup and the sidecar is uncapped")
    return rep


def main():
    run = Path(sys.argv[1])
    env = {}
    envf = run / "environment.txt"
    if envf.exists():
        env = dict(l.split("=", 1) for l in envf.read_text().splitlines() if "=" in l)
    cpus = int(env.get("nproc") or 0) or None

    out = {"run_dir": str(run), "mode": "blast", "environment": env, "arms": {}}
    for arm, name in ARMS.items():
        reps = sorted((run / arm).glob("rep*")) if (run / arm).exists() else []
        if not reps:
            continue
        rr = [rep_report(d, arm, cpus) for d in reps]
        good = [r for r in rr if not r.get("error")]
        det = None
        if len(good) >= 2:
            a, _, _ = load_records(reps[0] / "per_doc.jsonl")
            b, _, _ = load_records(reps[1] / "per_doc.jsonl")
            det = determinism(a, b)
        arm_out = {
            "reps": rr,
            "determinism_rep1_vs_rep2": det,
            "m0_PASS": (all(r.get("m0_PASS_partial") for r in good)
                        and gate_verdict(det) if det else False),
            "stability": across_reps(good, {
                "docs_per_s": ("m1_throughput", "docs_per_s"),
                "chunks_per_s": ("m1_throughput", "chunks_per_s"),
                "p50_batch_position_s": ("m2_latency", "p50"),
                "p95_batch_position_s": ("m2_latency", "p95"),
                "effective_cores": ("m7_resources", "effective_cores"),
                "cpu_utilization": ("m7_efficiency", "cpu_utilization"),
                "cpu_s_per_chunk": ("m7_efficiency", "cpu_seconds_per_chunk"),
                "peak_rss_mb": ("m7_resources", "rss_mb", "peak"),
                "achieved_peak": ("concurrency", "achieved_peak"),
            }),
        }
        out["arms"][name] = arm_out

    # ---- cross-arm: did the two frameworks do the SAME WORK? --------------
    # Every check above validates ONE arm against its own contract. Two arms
    # can each be perfect while processing different amounts of text, and then
    # comparing their throughput is meaningless. Uses rep1 of each arm.
    lg1, rr1 = run / "lg" / "rep1" / "per_doc.jsonl", run / "rr" / "rep1" / "per_doc.jsonl"
    if lg1.exists() and rr1.exists():
        a, _, _ = load_records(lg1)
        b, _, _ = load_records(rr1)
        # Byte parity is GATED only when both arms parse with Tika; with
        # different extractors identical hashes are not expected, and the
        # ratio bands carry the check instead.
        matched = (env.get("lg_extractor") == "tika")
        out["cross_arm"] = cross_arm(a, b, "lg", "rr", require_byte_parity=matched)
        out["cross_arm"]["note"] = (
            "byte parity gated (matched Tika extractors)" if matched else
            "byte parity measured but NOT gated (extractors differ)")
    else:
        out["cross_arm"] = {"PASS": False,
                            "error": "one arm missing — cannot prove equal work"}

    provf = run / "provenance.json"
    if provf.exists():
        out["provenance"] = prov_check(json.loads(provf.read_text()))

    (run / "BLAST_REPORT.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))

    print("\n" + "=" * 74)
    print(f"BLAST — {len(ARMS)} arms, warm-up excluded by driver, "
          f"latency = BATCH-POSITION (includes queue wait)")
    for name, a in out["arms"].items():
        s = a["stability"]
        print(f"\n{name}:  M0 {'PASS' if a['m0_PASS'] else 'FAIL'}")
        for k in ("docs_per_s", "chunks_per_s", "p95_batch_position_s",
                  "effective_cores", "cpu_utilization", "cpu_s_per_chunk",
                  "achieved_peak"):
            v = s.get(k, {})
            print(f"  {k:<24} median={v.get('median')}  cv={v.get('cv')}  "
                  f"{v.get('verdict')}")
    c = out.get("cross_arm", {})
    print(f"\ncross-arm : {'PASS' if c.get('PASS') else 'FAIL'}  "
          f"compared={c.get('compared')}  byte-identical={c.get('byte_identical')}"
          f"/{c.get('compared')}  ratio(rr/lg)={c.get('chunk_ratio_rr_over_lg')}")
    if c.get("hard_violations"):
        print(f"            HARD band {c.get('hard_band')} violated by "
              f"{len(c['hard_violations'])} docs: {c['hard_violations'][:8]}")
    if c.get("warn_violations"):
        print(f"            warn band {c.get('warn_band')}: "
              f"{len(c['warn_violations'])} docs (reported, not failing)")
    if c.get("note"):
        print(f"            {c['note']}")
    if "provenance" in out:
        p = out["provenance"]
        print(f"\nprovenance: {'complete' if p['PASS'] else 'INCOMPLETE ' + str(p['missing_fields'])}")
    print("=" * 74)
    ok = (bool(out["arms"])
          and all(a["m0_PASS"] for a in out["arms"].values())
          and out["cross_arm"].get("PASS") is True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
