"""Compute the frozen per-doc timeout from calibration data.

Formula (preregistered, from the handoff): max(60 s, calibration p99 x 5),
computed per arm from that arm's own 100-doc calibration burst, then FROZEN
— written to runs/pdf1k/frozen_params.json and never adjusted afterward.
"""

import json
import sys
from pathlib import Path


def p99(path):
    lats = []
    for line in Path(path).read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "client_meta":
            continue
        if "submit_ns" in r and "completion_ns" in r:
            lats.append((r["completion_ns"] - r["submit_ns"]) / 1e9)
    lats.sort()
    return lats[int(len(lats) * 0.99)] if lats else None


def main(lg_cal, rr_cal, out_path):
    lg = p99(lg_cal)
    rr = p99(rr_cal)
    frozen = {
        "formula": "max(60, calibration_p99 * 5) per arm — FROZEN before rep 1",
        "lg_calibration_p99_s": round(lg, 2) if lg else None,
        "rr_calibration_p99_s": round(rr, 2) if rr else None,
        "lg_timeout_s": max(60.0, round(lg * 5, 1)) if lg else 60.0,
        "rr_timeout_s": max(60.0, round(rr * 5, 1)) if rr else 60.0,
        "container_cpus": 12,
        "container_memory_gb": 12,
        "submission": "open-loop burst, all docs at once, no client cap",
        "chunk_size": "NOT SET (framework defaults; pinned inequality stands)",
    }
    Path(out_path).write_text(json.dumps(frozen, indent=2))
    print(json.dumps(frozen))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
