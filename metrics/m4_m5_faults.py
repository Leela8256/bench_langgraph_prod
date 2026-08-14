"""M4 blast radius + M5 fault isolation. Consumes a fault-injection run.

fault_manifest format (JSON): {"faults": [{"doc": "<name>", "type":
"corrupt|encrypted|zero_byte|no_text", "position": <corpus index>}, ...]}
Docs not in the manifest are "unrelated" — only they can count as collateral.
"""

from typing import Any, Dict, List, Optional

from metrics.records import by_completion, ok_records


def blast_radius(rows: List[Dict], fault_docs: List[str],
                 window_s: float = 60.0) -> Dict[str, Any]:
    """Per fault: collateral unrelated failures + time-to-next-success.

    Collateral = unrelated docs whose FAILURE completes within window_s
    after the fault doc's outcome (attribution window; wedges show up as
    long unbroken failure runs and are counted fully).
    """
    fault_set = set(fault_docs)
    done = by_completion(rows)
    out = {}
    for fd in fault_docs:
        frec = next((r for r in done if r["doc"] == fd), None)
        if frec is None:
            out[fd] = {"error": "fault doc has no record"}
            continue
        t_fault = frec["completion_ns"]
        collateral = [r["doc"] for r in done
                      if r["doc"] not in fault_set and not r.get("ok")
                      and 0 <= (r["completion_ns"] - t_fault) / 1e9 <= window_s]
        nxt = next((r for r in done if r["completion_ns"] > t_fault
                    and r.get("ok") and r["doc"] not in fault_set), None)
        out[fd] = {
            "fault_outcome": frec.get("reason") or ("ok" if frec.get("ok") else "failed"),
            "collateral_count": len(collateral),
            "collateral_docs": collateral[:20],
            "time_to_next_success_s":
                round((nxt["completion_ns"] - t_fault) / 1e9, 2) if nxt else None,
        }
    total = sum(v.get("collateral_count", 0) for v in out.values()
                if isinstance(v, dict))
    return {"per_fault": out, "total_collateral": total,
            "PASS_zero_blast": total == 0}


def fault_isolation(rows: List[Dict], fault_docs: List[str],
                    resources_before: Optional[Dict] = None,
                    resources_after: Optional[Dict] = None,
                    rss_tolerance_mb: float = 500.0) -> Dict[str, Any]:
    """M5: surfacing / continuity / restart / resource recovery."""
    fault_set = set(fault_docs)
    frecs = [r for r in rows if r["doc"] in fault_set]
    # SERVER-surfaced means the service itself communicated failure (an HTTP
    # error status or an error frame). A success-shaped empty response
    # ("no_documents") is NOT server-surfaced — only the client's completion
    # proof inferred the failure. Both flags are reported.
    def _server_surfaced(r):
        if r.get("http_status") and r["http_status"] >= 400:
            return True
        if r.get("reason") in ("no_documents",):
            return False  # success-shaped empty: silent from the server
        if r.get("reason") in ("transport_error",):
            return True
        return bool(r.get("error")) and r.get("reason") not in ("completed", None)

    surfaced = {r["doc"]: _server_surfaced(r)
                for r in frecs if not r.get("ok")}
    client_inferred = {r["doc"]: (not _server_surfaced(r))
                       for r in frecs if not r.get("ok")}
    unrelated_ok = len([r for r in ok_records(rows) if r["doc"] not in fault_set])
    unrelated_total = len([r for r in rows if r["doc"] not in fault_set])
    res = {}
    if resources_before and resources_after:
        growth = (resources_after.get("rss_mb", {}).get("end", 0)
                  - resources_before.get("rss_mb", {}).get("start", 0))
        res = {"rss_growth_mb": round(growth, 1),
               "recovered": abs(growth) < rss_tolerance_mb,
               "caveat": "valid only with pre/post-FAULT baselines; "
                         "run-boundary baselines conflate warmup growth"}
    return {
        "error_surfaced_by_server": surfaced,
        "failure_only_inferred_by_client": client_inferred,
        "all_errors_surfaced": all(surfaced.values()) if surfaced else None,
        "service_continued": unrelated_ok == unrelated_total,
        "unrelated_ok": f"{unrelated_ok}/{unrelated_total}",
        "restart_required": None,  # recorded by the run orchestrator, not derivable
        "resource_recovery": res or None,
    }
