"""Sample the NATIVE RocketRide engine's process tree on macOS.

Every 500 ms: for each process whose command mentions rocketride-native,
record pid, RSS (KB), %CPU, thread count. One JSON line per sample.

  /usr/bin/python3 gate50/host_sampler_native.py > gate50/out_rr/host_sampler.jsonl
"""

import json
import subprocess
import sys
import time


def sample():
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid,rss,%cpu,args"],
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    procs = []
    for line in out.splitlines()[1:]:
        if "rocketride-native" not in line or "host_sampler" in line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, rss, cpu = parts[0], parts[1], parts[2]
        try:
            thr = subprocess.run(["ps", "-M", pid], capture_output=True,
                                 text=True, timeout=5).stdout.count("\n") - 1
        except Exception:
            thr = -1
        procs.append({"pid": int(pid), "rss_mb": round(int(rss) / 1024, 1),
                      "cpu_pct": float(cpu), "threads": max(thr, 0),
                      "cmd": parts[3][:70]})
    return procs


def main():
    while True:
        t = time.time()
        procs = sample()
        if procs is not None:
            line = {"ts": round(t, 3),
                    "rss_mb_sum": round(sum(p["rss_mb"] for p in procs), 1),
                    "cpu_pct_sum": round(sum(p["cpu_pct"] for p in procs), 1),
                    "threads_sum": sum(p["threads"] for p in procs),
                    "n_procs": len(procs),
                    "procs": procs}
            sys.stdout.write(json.dumps(line) + "\n")
            sys.stdout.flush()
        time.sleep(max(0.0, 0.5 - (time.time() - t)))


if __name__ == "__main__":
    main()
