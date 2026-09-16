"""Merge supplementary cgroup sampler streams into a rep's sampler.jsonl.

Why: run/matched_run.sh starts its in-container sampler with SAMPLE_MAX_S=7200,
so a rep that runs longer than two hours (the 2026-09-16 b16 cell ran five)
loses CPU/RSS coverage for its tail. A supervisor on the box ran the SAME
sampler (bench/cgroup_sampler.py, same schema, same cgroup counters) without
the cap; this merges those streams so metrics.m7_resources.window() sees the
whole window. The runner's original stream is preserved beside the merge, and
a sidecar records exactly what was merged.

  python3 bench/merge_samplers.py <rep_dir> <extra.jsonl> [<extra.jsonl> ...]
"""

import json
import shutil
import sys
from pathlib import Path


def load(p: Path):
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "ts" in r and "cpu_total_s" in r:
            out.append(r)
    return out


def main():
    rep = Path(sys.argv[1])
    extras = [Path(x) for x in sys.argv[2:]]
    target = rep / "sampler.jsonl"
    keep = rep / "sampler.runner.jsonl"
    if not keep.exists():
        shutil.copy2(target, keep)          # original, untouched, kept forever
    base = load(keep)
    merged = {r["ts"]: r for r in base}
    counts = {"runner": len(base)}
    for e in extras:
        rows = load(e)
        counts[e.name] = len(rows)
        for r in rows:
            merged.setdefault(r["ts"], r)
    rows = [merged[k] for k in sorted(merged)]
    # A cumulative counter must never go backwards across the merge; if it
    # does, the streams are from different containers and must not be mixed.
    for x, y in zip(rows, rows[1:]):
        assert y["cpu_total_s"] >= x["cpu_total_s"] - 1e-6, (
            f"cpu_total_s not monotonic at ts={y['ts']}: streams from different containers?")
    with open(target, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    side = {"merged_from": counts, "n_merged": len(rows),
            "first_ts": rows[0]["ts"], "last_ts": rows[-1]["ts"],
            "note": "runner stream capped at SAMPLE_MAX_S=7200; supplementary "
                    "stream(s) from the same sampler code on the same container "
                    "(box supervisor, uncapped). Original kept as sampler.runner.jsonl."}
    (rep / "sampler_merge.json").write_text(json.dumps(side, indent=1))
    print(json.dumps(side, indent=1))


if __name__ == "__main__":
    main()
