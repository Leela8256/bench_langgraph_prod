"""100ms /proc sampler — runs INSIDE a container (no psutil needed).

Samples every process: RSS, cumulative CPU ticks, threads, ctxt switches.
One JSON line per interval to stdout; redirect to a file via docker exec.

  docker exec <c> python /work/proc_sampler.py > rep/sampler_<arm>.jsonl
"""

import json
import os
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def pids():
    return [int(d) for d in os.listdir("/proc") if d.isdigit()]


def sample_pid(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            parts = f.read().split(b")")[-1].split()
        # after comm: fields from index 0 = state; utime=11, stime=12,
        # num_threads=17, rss=21 (relative to post-comm split)
        utime, stime = int(parts[11]), int(parts[12])
        threads = int(parts[17])
        rss_pages = int(parts[21])
        ctxt = 0
        with open(f"/proc/{pid}/status", "rb") as f:
            for line in f:
                if line.startswith(b"voluntary_ctxt") or line.startswith(b"nonvoluntary_ctxt"):
                    ctxt += int(line.split()[-1])
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read(80).replace(b"\x00", b" ").decode(errors="replace").strip()
        return {
            "pid": pid,
            "cpu_s": round((utime + stime) / HZ, 3),
            "rss_mb": round(rss_pages * PAGE / 1048576, 1),
            "threads": threads,
            "ctxt": ctxt,
            "cmd": cmd[:60],
        }
    except Exception:
        return None


def total_cpu():
    with open("/proc/stat", "rb") as f:
        parts = f.readline().split()[1:8]
    return sum(int(x) for x in parts) / HZ


def main():
    interval = float(os.environ.get("SAMPLE_INTERVAL_S", "0.1"))
    while True:
        t = time.time()
        procs = [s for s in (sample_pid(p) for p in pids()) if s]
        line = {
            "ts": round(t, 3),
            "cpu_total_s": round(total_cpu(), 2),
            "rss_mb_sum": round(sum(p["rss_mb"] for p in procs), 1),
            "n_procs": len(procs),
            "n_threads": sum(p["threads"] for p in procs),
            "procs": [p for p in procs if p["rss_mb"] > 50 or p["cpu_s"] > 1],
        }
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()
        time.sleep(max(0.0, interval - (time.time() - t)))


if __name__ == "__main__":
    main()
