"""Synthetic-data tests for metric logic that has no real run yet
(determinism, blast radius, fault isolation, efficiency, parity) plus
fail-closed regression tests for the M0 gate: every scenario where absent
data used to pass silently must now fail.

  python3 -m metrics.test_synthetic
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics.m0_correctness import (census, determinism, gate_verdict,  # noqa: E402
                                    ground_truth_match, parity_fixture,
                                    structure)
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


def okrec(doc, arm="rr", n_chunks=3, **over):
    """A completed record satisfying the full per-arm structure contract."""
    r = {"doc": doc, "ok": True, "n_chunks": n_chunks,
         "chunk_sha256": [f"h{i}" for i in range(n_chunks)],
         "vector_dim": 384 if n_chunks else None,
         "l2_norms_minmax": [1.0, 1.0] if n_chunks else [0, 0],
         "identity_ok": True}
    if arm == "lg":
        r.update({"sha_header_ok": True, "vectors_finite": True})
    r.update(over)
    return r


def failrec(doc, reason=None, error=None):
    r = {"doc": doc, "ok": False}
    if reason:
        r["reason"] = reason
    if error:
        r["error"] = error
    return r


def m0_fail_closed(fails):
    # --- parser regression: all 50 docs complete with zero chunks. The old
    #     gate reported all-green here; structure must now fail 49 of them.
    rows = [okrec(f"d{i:02d}", n_chunks=0) for i in range(50)]
    s = structure(rows, "rr", expected_empty={"d00"})
    if s["PASS"] or len(s["bad_docs"]) != 49 or s["completed_empty"] != ["d00"]:
        fails.append(f"empty-regression not caught: PASS={s['PASS']} "
                     f"bad={len(s['bad_docs'])} empty={s['completed_empty']}")
    if "empty_not_allowlisted" not in s["bad_docs"].get("d01", []):
        fails.append(f"empty violation not named: {s['bad_docs'].get('d01')}")

    # --- missing identity_ok must fail, not default to pass
    r = okrec("x")
    del r["identity_ok"]
    if structure([r], "rr")["PASS"]:
        fails.append("missing identity_ok passed structure")
    if structure([okrec("x", identity_ok=False)], "rr")["PASS"]:
        fails.append("identity_ok=False passed structure")

    # --- LG contract: missing sha_header_ok / vectors_finite must fail;
    #     the same absent fields are fine on RR (not in its contract)
    r = okrec("x", arm="lg")
    del r["sha_header_ok"]
    if structure([r], "lg")["PASS"]:
        fails.append("lg missing sha_header_ok passed structure")
    r = okrec("x", arm="lg")
    del r["vectors_finite"]
    if structure([r], "lg")["PASS"]:
        fails.append("lg missing vectors_finite passed structure")
    if not structure([okrec("x")], "rr")["PASS"]:
        fails.append("rr contract record should pass structure")

    # --- chunk hash count must match n_chunks
    if structure([okrec("x", chunk_sha256=["h0"])], "rr")["PASS"]:
        fails.append("hash count != n_chunks passed structure")

    # --- allowlisted empty doc still needs identity
    r = okrec("e", n_chunks=0)
    del r["identity_ok"]
    if structure([r], "rr", expected_empty={"e"})["PASS"]:
        fails.append("allowlisted empty doc with no identity passed")

    # --- census with a manifest names the silent drop
    c = census([okrec("a"), okrec("b")], 3, expected_docs={"a", "b", "c"})
    if c["PASS"] or c["missing_docs"] != ["c"] or c["silent"] != 1:
        fails.append(f"silent drop not named: {c['missing_docs']}")
    c = census([okrec("a"), okrec("z")], 2, expected_docs={"a", "b"})
    if c["PASS"] or c["unexpected_docs"] != ["z"]:
        fails.append(f"unexpected doc not named: {c['unexpected_docs']}")

    # --- unexpected failures gate census; expected-empty no_documents doesn't
    c = census([okrec("a"), failrec("000164.pdf", reason="no_documents")], 2,
               expected_empty={"000164.pdf"})
    if not c["PASS"] or c["unexpected_failures"]:
        fails.append(f"expected-empty failure should be excused: {c}")
    c = census([okrec("a"), failrec("000164.pdf", reason="timeout")], 2,
               expected_empty={"000164.pdf"})
    if c["PASS"] or c["unexpected_failures"] != 1:
        fails.append("timeout on allowlisted doc should still fail census")
    c = census([failrec(f"d{i}", reason="no_documents") for i in range(50)], 50)
    if c["PASS"]:
        fails.append("50 unexpected failures passed census")

    # --- failure bucketing: exception type, with distinct full messages kept
    c = census([failrec("a", error="ConnErr: recv 1011 (internal error); then sent 1011"),
                failrec("b", error="ConnErr: recv 1011 (internal error); no close frame")],
               2)
    if list(c["failed_by_reason"]) != ["ConnErr"]:
        fails.append(f"error-type bucketing wrong: {c['failed_by_reason']}")
    if len(c["failure_examples"]["ConnErr"]) != 2:
        fails.append(f"distinct messages collapsed: {c['failure_examples']}")

    # --- determinism: outcome flip between runs must fail
    d = determinism([rec("d1", 0, 1), rec("d2", 0, 2)], [rec("d1", 0, 1)])
    if d["PASS"] or d["only_in_a"] != ["d2"]:
        fails.append(f"outcome flip not caught: {d}")
    # hashes absent on both sides is unproven, not equal
    d = determinism([rec("d1", 0, 1, hashes=[]) | {"chunk_sha256": None}],
                    [rec("d1", 0, 1, hashes=[]) | {"chunk_sha256": None}])
    if d["PASS"]:
        fails.append("None==None chunk hashes passed determinism")

    # --- ground truth: zero coverage is not a pass
    if ground_truth_match([okrec("a")], {})["PASS"]:
        fails.append("empty ground truth passed vacuously")

    # --- verdict: PENDING strings and None can never aggregate to pass
    if gate_verdict({"PASS": True}, {"PASS": "PENDING (needs sequential pass)"}):
        fails.append("truthy-string PASS leaked through gate_verdict")
    if gate_verdict({"PASS": True}, {"PASS": None}) or gate_verdict({"PASS": True}, None):
        fails.append("None PASS leaked through gate_verdict")
    if not gate_verdict({"PASS": True}, {"PASS": True}):
        fails.append("all-True gate_verdict should pass")


def main() -> int:
    fails = []

    m0_fail_closed(fails)

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
    print("SYNTHETIC TESTS PASS: M0 fail-closed gate, determinism, blast "
          "radius, fault isolation, efficiency, parity fixture all behave "
          "per definition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
