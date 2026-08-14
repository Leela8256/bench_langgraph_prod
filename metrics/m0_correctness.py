"""M0 — the correctness gate. See README for the logic of each check."""

from typing import Any, Dict, Iterable, List, Optional, Set

from metrics.records import ok_records

NORM_TOL = 1e-3
EMBED_DIM = 384


def census(rows: List[Dict], offered: int,
           expected_empty: Set[str] = frozenset()) -> Dict[str, Any]:
    docs = [r["doc"] for r in rows]
    ok = ok_records(rows)
    fails = [r for r in rows if not r.get("ok")]
    by_reason: Dict[str, List[str]] = {}
    for r in fails:
        reason = r.get("reason") or (r.get("error") or "unknown")[:40]
        by_reason.setdefault(reason, []).append(r["doc"])
    unexpected = [d for ds in by_reason.values() for d in ds
                  if d not in expected_empty]
    return {
        "offered": offered,
        "records": len(rows),
        "silent": offered - len(rows),
        "unique": len(set(docs)) == len(docs),
        "completed": len(ok),
        "failed_by_reason": {k: len(v) for k, v in by_reason.items()},
        "failed_docs": by_reason,
        "unexpected_failures": len(unexpected),
        "PASS": len(rows) == offered and len(set(docs)) == len(docs),
    }


def structure(rows: List[Dict], arm: str) -> Dict[str, Any]:
    bad, empty = [], []
    for r in ok_records(rows):
        if r.get("n_chunks", 0) == 0:
            empty.append(r["doc"])  # completed-empty: valid for no-text docs
            continue
        mn, mx = (r.get("l2_norms_minmax") or [0, 0])
        good = (r.get("vector_dim") == EMBED_DIM
                and abs(mn - 1) < NORM_TOL and abs(mx - 1) < NORM_TOL
                and r.get("identity_ok", True)
                and r.get("vectors_finite", True))
        if arm == "lg" and r.get("sha_header_ok") is False:
            good = False
        if not good:
            bad.append(r["doc"])
    return {"bad_docs": bad, "completed_empty": empty, "PASS": not bad}


def determinism(rows_a: List[Dict], rows_b: List[Dict]) -> Dict[str, Any]:
    """Run-over-run: ordered chunk hashes must match per doc."""
    a = {r["doc"]: r.get("chunk_sha256") for r in ok_records(rows_a)}
    b = {r["doc"]: r.get("chunk_sha256") for r in ok_records(rows_b)}
    both = sorted(set(a) & set(b))
    mismatch = [d for d in both if a[d] != b[d]]
    return {"compared": len(both), "mismatch_docs": mismatch,
            "only_in_a": sorted(set(a) - set(b)),
            "only_in_b": sorted(set(b) - set(a)),
            "PASS": bool(both) and not mismatch}


def ground_truth_match(rows: List[Dict], gt: Dict[str, Dict],
                       check_offsets: bool = False) -> Dict[str, Any]:
    """Byte tier: chunk hashes (and optionally offsets) vs a reference."""
    covered = mism = 0
    mismatch_docs = []
    for r in ok_records(rows):
        g = gt.get(r["doc"])
        if not g:
            continue
        covered += 1
        bad = r.get("chunk_sha256") != g.get("chunk_sha256")
        if check_offsets and not bad:
            bad = r.get("offsets") != g.get("offsets")
        if bad:
            mism += 1
            mismatch_docs.append(r["doc"])
    return {"covered": covered, "mismatches": mism,
            "mismatch_docs": mismatch_docs[:15], "PASS": mism == 0}


def parity_fixture(vec_a: Optional[List[float]], vec_b: Optional[List[float]],
                   atol: float = 1e-5) -> Dict[str, Any]:
    if not (isinstance(vec_a, list) and isinstance(vec_b, list)
            and len(vec_a) == len(vec_b) and vec_a):
        return {"PASS": False, "error": "vector missing or length mismatch"}
    worst = max(abs(x - y) for x, y in zip(vec_a, vec_b))
    return {"max_abs_diff": worst, "atol": atol, "PASS": worst < atol}


def gate_verdict(*checks: Dict[str, Any]) -> bool:
    return all(c.get("PASS") for c in checks)
