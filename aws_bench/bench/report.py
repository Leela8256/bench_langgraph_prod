"""Run report — per-rep metrics, across-rep stability, cross-arm parity.

Mode-aware. `blast` and `c<N>` are different experiments and are labelled as
such: closed-loop yields SERVICE latency, blast yields BATCH-POSITION latency
(queue wait included). They answer different questions and are never compared.

Nothing is computed here that metrics/ can compute. This file decides WHAT to
ask and WHETHER the run passes; the formulas live in the library.

  python3 bench/report.py <run_dir>
Expects <run_dir>/<arm>/rep<N>/per_doc.jsonl.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # aws_bench/
sys.path.insert(0, str(ROOT))

from metrics.m0_correctness import (census, cross_arm, determinism,
                                    gate_verdict, input_integrity,
                                    self_duplication, structure)
from metrics.m1_m2_perf import achieved_concurrency, latency, throughput, ttfr
from metrics.m7_resources import (combined_peak_rss, container_resources,
                                  efficiency, window)
from metrics.provenance import check as prov_check
from metrics.records import load_records, ok_records
from metrics.stability import across_reps

# Warm-up is excluded by the DRIVER (real docs, timed separately), so no
# further window slicing is applied here in either mode.
WARM_N = 0
EXPECTED_EMPTY = {"000164.pdf"}
ARMS = {"lg": "langgraph", "rr": "rocketride"}


def load_manifest_sha(run: Path) -> dict:
    """corpus.sha256 is `<sha>  <name>` per line — the canonical inputs."""
    f = run / "corpus.sha256"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[Path(parts[-1]).name] = parts[0]
    return out


def rep_report(d: Path, arm: str, cpus, arm_cpus, mode: str,
               manifest_sha: dict) -> dict:
    f = d / "per_doc.jsonl"
    if not f.exists():
        return {"error": f"missing {f}"}
    rows, meta, _ = load_records(f)
    manifest = json.loads((d / "manifest.json").read_text())
    meta = meta or {}
    closed_loop = mode.startswith("c") and mode[1:].isdigit()
    # A batch response carries no per-document timestamps, so the driver marks
    # what clock each record used. Latency is publishable only when the times
    # were CLIENT-OBSERVED; values reconstructed from the engine's own
    # upload_time are withheld. Judged over all records, not just the first,
    # since a failed record may carry no marker at all.
    sources = {str(r.get("timing_source", "")) for r in rows}
    client_observed = any("client-observed" in x for x in sources)
    derived = any("derived" in x for x in sources) and not client_observed

    rep = {"rep_dir": d.name, "mode": mode, "meta": meta}
    rep["census"] = census(rows, offered=manifest["n"],
                           expected_docs=set(manifest["docs"]),
                           expected_empty=EXPECTED_EMPTY)
    rep["structure"] = structure(rows, arm=arm, expected_empty=EXPECTED_EMPTY)
    rep["input_integrity"] = input_integrity(rows, manifest_sha)
    # Permanent gate: an arm that emits its document list twice passes every
    # per-document check while doing twice the work.
    rep["self_duplication"] = self_duplication(rows)
    rep["m0_PASS_partial"] = gate_verdict(rep["census"], rep["structure"],
                                          rep["input_integrity"],
                                          rep["self_duplication"])

    t = throughput(rows, warm_n=WARM_N)
    # In batch mode the driver's own measured makespan is authoritative,
    # whichever clock the per-document records used.
    if (derived or (not closed_loop and client_observed)) and meta.get("span_s"):
        span = float(meta["span_s"])
        ok_n = t.get("successful_in_window")
        chunks = t.get("successful_chunks")
        t.update({
            "window_span_s": round(span, 3),
            "docs_per_s": round(ok_n / span, 4) if span else None,
            "chunks_per_s": round(chunks / span, 4) if span else None,
            "span_source": "MEASURED batch makespan (shot_meta.span_s)",
        })
    rep["m1_throughput"] = t
    rep["ttfr_s"] = ttfr(rows)
    rep["timing_source"] = sorted(x for x in sources if x)

    if derived:
        # Withheld rather than published: percentiles over SDK-derived
        # durations are not client-observed latency, and an interval sweep
        # over them reconstructs the offered batch shape rather than proving
        # server-side execution concurrency.
        why = ("UNAVAILABLE — batch mode returns no per-document timestamps; "
               "derived values would not be client-observed")
        rep["m2_latency"] = {"unavailable": why}
        rep["concurrency"] = {"offered": meta.get("offered_concurrency"),
                              "achieved_peak": None,
                              "achieved_unavailable": why}
    else:
        lat = latency(rows, warm_n=WARM_N,
                      mode="closed-loop" if closed_loop else "open-loop-blast")
        lat["label"] = ("SERVICE LATENCY" if closed_loop
                        else "BATCH-POSITION LATENCY — includes queue wait")
        rep["m2_latency"] = lat
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
        w = window(sampler, t.get("window_t0_ns"), t.get("window_t1_ns"),
                   meta.get("mono_offset_ns"))
        rep["m7_resources"] = w or container_resources(sampler)
        rep["m7_window"] = ("sliced to throughput window" if w else
                            "WHOLE STREAM — not window-matched")
        res = rep.get("m7_resources") or {}
        if res:
            rep["m7_efficiency"] = efficiency(
                t.get("successful_in_window"), res.get("cpu_seconds"),
                t.get("successful_chunks"), t.get("window_span_s"), cpus,
                arm_cpus)

    tika = d / "sampler_tika.jsonl"
    if tika.exists():
        # LangGraph PARSES in the sidecar, a different cgroup. RocketRide is
        # charged for its own embedded Tika, so omitting this compares a
        # parsing framework against a non-parsing one.
        tk = window(tika, t.get("window_t0_ns"), t.get("window_t1_ns"),
                    meta.get("mono_offset_ns")) or container_resources(tika)
        rep["m7_resources_tika_sidecar"] = tk
        svc = rep.get("m7_resources") or {}
        if tk and svc:
            total_cpu = round((svc.get("cpu_seconds") or 0)
                              + (tk.get("cpu_seconds") or 0), 2)
            span = svc.get("span_s") or t.get("window_span_s")
            combined = combined_peak_rss(
                sampler, tika, t.get("window_t0_ns"), t.get("window_t1_ns"),
                meta.get("mono_offset_ns"))
            rep["m7_arm_total"] = {
                "cpu_seconds": total_cpu,
                "effective_cores": round(total_cpu / span, 3) if span else None,
                "components": {"service": svc.get("cpu_seconds"),
                               "tika_sidecar": tk.get("cpu_seconds")},
                "peak_rss_mb_contemporaneous": combined,
            }
            rep["m7_efficiency_service_only"] = rep.get("m7_efficiency")
            rep["m7_efficiency"] = efficiency(
                t.get("successful_in_window"), total_cpu,
                t.get("successful_chunks"), t.get("window_span_s"), cpus,
                arm_cpus)
            rep["m7_note"] = ("ARM TOTAL (langgraph + tika) feeds efficiency; "
                              "peak RSS is the max SIMULTANEOUS sum, not "
                              "peak(svc) + peak(tika)")
    return rep


def arm_report(run: Path, arm: str, cpus, arm_cpus, mode: str,
               manifest_sha: dict, want_reps: int) -> dict:
    reps = sorted((run / arm).glob("rep*"))
    rr = [rep_report(d, arm, cpus, arm_cpus, mode, manifest_sha) for d in reps]
    good = [r for r in rr if not r.get("error")]

    # EVERY rep against rep1, not just rep2 — rep3 was previously free to
    # differ without failing anything.
    dets, det_pass = {}, None
    if len(reps) >= 2:
        base, _, _ = load_records(reps[0] / "per_doc.jsonl")
        for other in reps[1:]:
            rows_b, _, _ = load_records(other / "per_doc.jsonl")
            dets[f"rep1_vs_{other.name}"] = determinism(base, rows_b)
        det_pass = all(d.get("PASS") is True for d in dets.values())

    rep_count_ok = (want_reps is None) or (len(reps) == want_reps)
    return {
        "reps": rr,
        "rep_count": len(reps),
        "rep_count_expected": want_reps,
        "rep_count_ok": rep_count_ok,
        "determinism": dets or None,
        "determinism_PASS": det_pass,
        # Fail-closed: fewer than 2 reps means determinism is UNPROVEN.
        "m0_PASS": bool(good) and rep_count_ok
                   and all(r.get("m0_PASS_partial") for r in good)
                   and det_pass is True,
        "stability": across_reps(good, {
            "docs_per_s": ("m1_throughput", "docs_per_s"),
            "chunks_per_s": ("m1_throughput", "chunks_per_s"),
            "p50_s": ("m2_latency", "p50"),
            "p95_s": ("m2_latency", "p95"),
            "effective_cores": ("m7_resources", "effective_cores"),
            "cpu_utilization": ("m7_efficiency", "cpu_utilization"),
            "cpu_s_per_chunk": ("m7_efficiency", "cpu_seconds_per_chunk"),
            "peak_rss_mb": ("m7_resources", "rss_mb", "peak"),
        }),
    }


def main():
    run = Path(sys.argv[1])
    env = {}
    envf = run / "environment.txt"
    if envf.exists():
        env = dict(l.split("=", 1) for l in envf.read_text().splitlines()
                   if "=" in l)
    cpus = int(env.get("nproc") or 0) or None
    arm_cpus = float(env.get("arm_cpus") or 0) or None
    mode = env.get("mode") or "blast"
    want_reps = int(env.get("reps") or 0) or None
    manifest_sha = load_manifest_sha(run)

    out = {"run_dir": str(run), "mode": mode, "environment": env, "arms": {}}
    for arm, name in ARMS.items():
        if not (run / arm).exists():
            continue
        out["arms"][name] = arm_report(run, arm, cpus, arm_cpus, mode,
                                       manifest_sha, want_reps)

    # ---- cross-arm, EVERY rep ------------------------------------------
    matched = (env.get("lg_extractor") == "tika")
    per_rep, n = {}, 0
    while True:
        n += 1
        lg = run / "lg" / f"rep{n}" / "per_doc.jsonl"
        rr = run / "rr" / f"rep{n}" / "per_doc.jsonl"
        if not (lg.exists() and rr.exists()):
            break
        a, _, _ = load_records(lg)
        b, _, _ = load_records(rr)
        per_rep[f"rep{n}"] = cross_arm(a, b, "lg", "rr",
                                       require_byte_parity=matched)
    out["cross_arm"] = {
        "per_rep": per_rep,
        "byte_parity_gated": matched,
        "note": ("byte parity gated (matched Tika extractors)" if matched else
                 "byte parity measured but NOT gated (extractors differ)"),
        "PASS": bool(per_rep) and all(v.get("PASS") is True
                                      for v in per_rep.values()),
    }
    if not per_rep:
        out["cross_arm"]["error"] = "no rep present on both arms"

    provf = run / "provenance.json"
    out["provenance"] = (prov_check(json.loads(provf.read_text()))
                         if provf.exists()
                         else {"PASS": False, "error": "provenance.json missing"})

    (run / "RUN_REPORT.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))

    closed = mode.startswith("c") and mode[1:].isdigit()
    print("\n" + "=" * 74)
    print(f"MODE {mode.upper()} — latency is "
          f"{'SERVICE (closed-loop)' if closed else 'BATCH-POSITION (blast)'}"
          f"; warm-up excluded by the driver")
    for name, a in out["arms"].items():
        s = a["stability"]
        dup = (a["reps"][0] or {}).get("self_duplication") or {}
        print(f"\n{name}:  M0 {'PASS' if a['m0_PASS'] else 'FAIL'}   "
              f"reps={a['rep_count']}/{a['rep_count_expected']}   "
              f"determinism={a['determinism_PASS']}   "
              f"self_dup={dup.get('duplicated_docs')}/{dup.get('checked')} "
              f"factors={dup.get('factors')}")
        for k in ("docs_per_s", "chunks_per_s", "p95_s", "effective_cores",
                  "cpu_utilization", "cpu_s_per_chunk"):
            v = s.get(k, {})
            if v.get("n"):
                print(f"  {k:<20} median={v.get('median')}  cv={v.get('cv')}  "
                      f"{v.get('verdict')}")
        u = (a["reps"][0] or {}).get("m2_latency", {}).get("unavailable")
        if u:
            print(f"  latency/concurrency  {u}")
    c = out["cross_arm"]
    print(f"\ncross-arm : {'PASS' if c['PASS'] else 'FAIL'}  ({len(c['per_rep'])} reps compared)")
    for rep, v in c["per_rep"].items():
        print(f"  {rep}: byte-identical {v['byte_identical']}/{v['compared']}  "
              f"ratio={v.get('chunk_ratio_rr_over_lg', {}).get('median')}  "
              f"hard_viol={len(v.get('hard_violations') or [])}")
    p = out["provenance"]
    print(f"\nprovenance: {'complete' if p['PASS'] else 'INCOMPLETE — ' + str(p.get('missing_fields') or p.get('error'))}")
    print("=" * 74)

    ok = (bool(out["arms"])
          and all(a["m0_PASS"] for a in out["arms"].values())
          and c["PASS"] is True
          and p["PASS"] is True)      # incomplete provenance is not publishable
    print("RUN PASS" if ok else "RUN FAIL — numbers kept, never quoted")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
