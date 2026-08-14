"""Smoke-2 report — BOTH arms, warm_n=25, plus cross-arm comparison.

Every metric comes from metrics/; nothing is computed here. Cross-arm chunk
equality is computed inline because metrics/ has no cross-arm module yet --
it is a comparison OF two arms' records, not a metric OF one arm.

  python3 aws_run/box/smoke2_report.py <run_dir>
Expects <run_dir>/{lg,rr}/pass{1,2}/per_doc.jsonl. Run from the repo root.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from metrics.m0_correctness import census, determinism, gate_verdict, structure
from metrics.m1_m2_perf import latency, throughput, ttfr
from metrics.m7_resources import container_resources, efficiency
from metrics.records import load_records, ok_records

WARM_N = 25          # AWS team directive, metrics/README M1
EXPECTED_EMPTY = {"000164.pdf"}
ARMS = {"lg": "langgraph", "rr": "rocketride"}


def arm_report(run: Path, arm: str) -> dict:
    p1, p2 = run / arm / "pass1", run / arm / "pass2"
    if not (p1 / "per_doc.jsonl").exists():
        return {"error": f"missing {p1}/per_doc.jsonl"}
    rows, meta, _ = load_records(p1 / "per_doc.jsonl")
    manifest = json.loads((p1 / "manifest.json").read_text())

    rep = {"arm": ARMS[arm], "meta": meta}
    rep["census"] = census(rows, offered=manifest["n"],
                           expected_docs=set(manifest["docs"]),
                           expected_empty=EXPECTED_EMPTY)
    rep["structure"] = structure(rows, arm=arm, expected_empty=EXPECTED_EMPTY)

    det = None
    if (p2 / "per_doc.jsonl").exists():
        rows_b, _, _ = load_records(p2 / "per_doc.jsonl")
        det = determinism(rows, rows_b)
        rep["determinism"] = det
    rep["m0_PASS"] = gate_verdict(rep["census"], rep["structure"], det)
    if det is None:
        rep["m0_note"] = "determinism NOT run — gate incomplete, fails closed"

    rep["m1_throughput"] = throughput(rows, warm_n=WARM_N)
    rep["m2_latency"] = latency(rows, warm_n=WARM_N, mode="closed-loop")
    rep["ttfr_s"] = ttfr(rows)
    # Also at warm_n=0 so the effect of the exclusion is visible, not assumed.
    rep["m1_throughput_warm0"] = throughput(rows, warm_n=0)

    sampler = p1 / "sampler.jsonl"
    if sampler.exists():
        res = container_resources(sampler)
        rep["m7_resources"] = res
        if res:
            rep["m7_efficiency"] = efficiency(
                rep["m1_throughput"].get("successful_in_window"),
                res["cpu_seconds"])
    # LG in tika mode parses inside the SEPARATE tika container, so its parse
    # CPU is NOT in the langgraph cgroup. Reported alongside, never silently
    # merged -- and tika is currently uncapped while both arms are capped at 12.
    tika = p1 / "sampler_tika.jsonl"
    if tika.exists():
        rep["m7_resources_tika_sidecar"] = container_resources(tika)
        rep["m7_note"] = ("LG parse CPU lives in the tika sidecar; add "
                          "m7_resources_tika_sidecar to compare like for like")
    return rep


def cross_arm(run: Path) -> dict:
    def hashes(arm):
        p = run / arm / "pass1" / "per_doc.jsonl"
        if not p.exists():
            return {}
        rows, _, _ = load_records(p)
        return {r["doc"]: r.get("chunk_sha256") for r in ok_records(rows)}

    lg, rr = hashes("lg"), hashes("rr")
    both = sorted(set(lg) & set(rr))
    identical = [d for d in both if lg[d] is not None and lg[d] == rr[d]]
    differing = [d for d in both if d not in set(identical)]

    def chunks(arm):
        p = run / arm / "pass1" / "per_doc.jsonl"
        rows, _, _ = load_records(p)
        return {r["doc"]: (r.get("n_chunks"), r.get("total_chars"))
                for r in ok_records(rows)}

    clg, crr = chunks("lg") if lg else {}, chunks("rr") if rr else {}
    ratios = [crr[d][0] / clg[d][0] for d in both
              if clg.get(d) and crr.get(d) and clg[d][0]]
    ratios.sort()
    return {
        "both_ok": len(both),
        "chunk_hashes_identical": len(identical),
        "chunk_hashes_differing": len(differing),
        "differing_docs": differing[:15],
        "byte_parity": bool(both) and not differing,
        "chunk_ratio_rr_over_lg": {
            "median": round(ratios[len(ratios) // 2], 4) if ratios else None,
            "min": round(min(ratios), 4) if ratios else None,
            "max": round(max(ratios), 4) if ratios else None,
        },
    }


def main():
    run = Path(sys.argv[1])
    rep = {"run_dir": str(run), "warm_n": WARM_N,
           "arms": {a: arm_report(run, a) for a in ARMS}}
    rep["cross_arm"] = cross_arm(run)
    rep["overall_PASS"] = all(
        r.get("m0_PASS") is True for r in rep["arms"].values())

    (run / "SMOKE2_REPORT.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps(rep, indent=1, default=str))

    print("\n" + "=" * 66)
    for a, r in rep["arms"].items():
        if r.get("error"):
            print(f"{ARMS[a]:<12} : ERROR {r['error']}")
            continue
        t, l = r["m1_throughput"], r["m2_latency"]
        res = r.get("m7_resources") or {}
        print(f"{ARMS[a]:<12} : M0 {'PASS' if r['m0_PASS'] else 'FAIL'}  "
              f"M1 {t.get('docs_per_s')} docs/s  "
              f"M2 p50={l.get('p50')}s p95={l.get('p95')}s  "
              f"M7 {res.get('effective_cores')} cores / "
              f"{(res.get('rss_mb') or {}).get('peak')} MB")
    c = rep["cross_arm"]
    print(f"cross-arm    : {c['chunk_hashes_identical']}/{c['both_ok']} docs "
          f"byte-identical, chunk ratio rr/lg median "
          f"{c['chunk_ratio_rr_over_lg']['median']}")
    print(f"OVERALL      : {'PASS' if rep['overall_PASS'] else 'FAIL'} "
          f"(warm_n={WARM_N})")
    print("=" * 66)
    return 0 if rep["overall_PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
