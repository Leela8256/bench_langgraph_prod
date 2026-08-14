"""Synthetic-data tests for metric logic that has no real run yet
(determinism, blast radius, fault isolation, efficiency, parity).

  python3 -m metrics.test_synthetic
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics.m0_correctness import determinism, parity_fixture  # noqa: E402
from metrics.m4_m5_faults import blast_radius, fault_isolation  # noqa: E402
from metrics.m7_resources import efficiency  # noqa: E402

S = 1_000_000_000  # 1s in ns


def rec(doc, t_sub, t_done, ok=True, hashes=None, reason=None, error=None):
    r = {"doc": doc, "submit_ns": t_sub * S, "completion_ns": t_done * S,
         "ok": ok, "chunk_sha256": hashes or [f"h-{doc}"]}
    if reason:
        r["reason"] = reason
    if error:
        r["error"] = error
    return r


def main() -> int:
    fails = []

    # --- determinism: identical -> PASS; one mutated hash -> FAIL that doc
    a = [rec("d1", 0, 1), rec("d2", 0, 2)]
    b = [rec("d1", 0, 1), rec("d2", 0, 2)]
    assert determinism(a, b)["PASS"], "identical runs must PASS"
    b2 = [rec("d1", 0, 1), rec("d2", 0, 2, hashes=["DIFFERENT"])]
    d = determinism(a, b2)
    if d["PASS"] or d["mismatch_docs"] != ["d2"]:
        fails.append(f"determinism mutation not caught: {d}")

    # --- blast radius: fault at t=10; two unrelated failures inside 60s
    #     window; one outside; next success at t=15
    rows = [
        rec("good1", 0, 5),
        rec("FAULT", 0, 10, ok=False, reason="corrupt"),
        rec("victim1", 0, 12, ok=False, reason="timeout"),
        rec("victim2", 0, 40, ok=False, reason="timeout"),
        rec("good2", 0, 15),
        rec("late_fail", 0, 200, ok=False, reason="timeout"),  # outside window
    ]
    br = blast_radius(rows, ["FAULT"], window_s=60)
    pf = br["per_fault"]["FAULT"]
    if pf["collateral_count"] != 2 or set(pf["collateral_docs"]) != {"victim1", "victim2"}:
        fails.append(f"blast radius collateral wrong: {pf}")
    if pf["time_to_next_success_s"] != 5.0:
        fails.append(f"time_to_next_success wrong: {pf['time_to_next_success_s']}")
    if br["PASS_zero_blast"]:
        fails.append("PASS_zero_blast should be False with collateral present")

    # zero-blast case
    br0 = blast_radius([rec("g", 0, 1), rec("F", 0, 2, ok=False, reason="corrupt"),
                        rec("g2", 0, 3)], ["F"])
    if not br0["PASS_zero_blast"]:
        fails.append("zero-blast case should PASS")

    # --- fault isolation: surfaced error, service continued
    fi = fault_isolation(
        [rec("F", 0, 1, ok=False, reason="corrupt", error="bad xref"),
         rec("g1", 0, 2), rec("g2", 0, 3)],
        ["F"],
        resources_before={"rss_mb": {"start": 1000}},
        resources_after={"rss_mb": {"end": 1100}},
    )
    if fi["all_errors_surfaced"] is not True:
        fails.append(f"error surfacing wrong: {fi}")

    # success-shaped empty (no_documents) must count as NOT server-surfaced
    fi_ns = fault_isolation([rec("F2", 0, 1, ok=False, reason="no_documents")], ["F2"])
    if fi_ns["all_errors_surfaced"] is not False:
        fails.append(f"no_documents should be silent-from-server: {fi_ns}")
    if fi["service_continued"] is not True:
        fails.append("service_continued should be True")
    if fi["resource_recovery"]["recovered"] is not True:
        fails.append("rss growth 100MB within 500MB tolerance should recover")

    # silent failure (no error text, reason=None but ok False with reason absent)
    fi2 = fault_isolation([{"doc": "F", "submit_ns": 0, "completion_ns": S,
                            "ok": False, "chunk_sha256": []}], ["F"])
    if fi2["all_errors_surfaced"] is not False:
        fails.append(f"silent failure should show error_surfaced=False: {fi2}")

    # --- efficiency arithmetic
    e = efficiency(successful_docs=30, cpu_seconds=120.0)
    if e["docs_per_cpu_second"] != 0.25 or e["cpu_seconds_per_doc"] != 4.0:
        fails.append(f"efficiency arithmetic wrong: {e}")

    # --- parity fixture
    if not parity_fixture([0.1, 0.2], [0.1, 0.2 + 1e-8])["PASS"]:
        fails.append("parity should PASS at 1e-8 diff")
    if parity_fixture([0.1, 0.2], [0.1, 0.3])["PASS"]:
        fails.append("parity should FAIL at 0.1 diff")

    if fails:
        print("SYNTHETIC TEST FAILURES:")
        for f in fails:
            print(" -", f)
        return 1
    print("SYNTHETIC TESTS PASS: determinism, blast radius, fault isolation, "
          "efficiency, parity fixture all behave per definition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
