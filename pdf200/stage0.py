"""Stage 0 — pipeline-token sharing check + environment facts.

Preregistered decision rule:
  shared instance (same token or no new engine workers) -> pool_size=8
  per-client instances -> pool_size=4, recorded product finding.

Writes runs/pdf200/stage0_decision.json and stage0_report.json.

  .rrclient/bin/python pdf200/stage0.py
"""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "runs" / "pdf200"
OUT.mkdir(parents=True, exist_ok=True)
URI = "ws://localhost:5565/task/service"


def engine_procs():
    r = subprocess.run(
        ["docker", "exec", "prodbench-rocketride", "sh", "-c",
         "ps ax -o pid,nlwp,rss,args | grep -E 'engine|node.py' | grep -v grep"],
        capture_output=True, text=True)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    total_threads = sum(int(l.split()[1]) for l in lines if l.split()[1].isdigit())
    return {"n_procs": len(lines), "total_threads": total_threads,
            "detail": lines[:10]}


async def main():
    from rocketride import RocketRideClient

    report = {"before_any_use": engine_procs()}

    pipe = json.loads((ROOT / "rocketride" / "benchmark_pdf.pipe").read_text())
    pipe["project_id"] = str(uuid.uuid4())
    pipe_path = OUT / "shared_pipeline.pipe"
    pipe_path.write_text(json.dumps(pipe))

    clients, tokens = [], []
    for i in range(3):
        c = RocketRideClient(uri=URI, auth="local-dev")
        await c.connect()
        used = await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        clients.append(c)
        tokens.append(used.get("token"))
        report[f"after_use_{i+1}"] = engine_procs()
        report[f"use_{i+1}"] = {k: used.get(k) for k in
                                ("token", "id", "projectId", "provider")}

    same_token = len(set(tokens)) == 1
    p1 = report["after_use_1"]["n_procs"]
    p3 = report["after_use_3"]["n_procs"]
    new_workers = p3 - p1
    shared = same_token or new_workers == 0
    decision = {
        "tokens": tokens,
        "same_token": same_token,
        "engine_procs_after_1st_use": p1,
        "engine_procs_after_3rd_use": p3,
        "new_workers_from_2nd_3rd_use": new_workers,
        "shared_instance": shared,
        "pool_size": 8 if shared else 4,
        "note": ("shared pipeline instance -> pool 8; footprint is default "
                 "RocketRide threadCount 64" if shared else
                 "PER-CLIENT INSTANCES (product finding): pool capped at 4; "
                 "pipelines=4; configured aggregate=4x64=256 threads; report "
                 "must not call this a single default instance"),
        "pipe_path": str(pipe_path),
        "project_id": pipe["project_id"],
    }
    # persist pool state so drivers reuse the same shared pipeline
    (OUT / "stage0_decision.json").write_text(json.dumps(decision, indent=1))
    (OUT / "rr_pool_state.json").write_text(json.dumps(
        {"pipe_path": str(pipe_path), "project_id": pipe["project_id"]}))

    for c in clients:
        try:
            await c.disconnect()
        except Exception:
            pass

    # LG side facts
    lg_exec = subprocess.run(
        ["docker", "exec", "prodbench-langgraph", "python", "-c",
         "import os; print(os.cpu_count(), min(32, (os.cpu_count() or 1) + 4))"],
        capture_output=True, text=True).stdout.split()
    report["lg"] = {
        "serving_layer": "library behind minimal FastAPI wrapper "
                         "(uvicorn 1 worker, own defaults)",
        "os_cpu_count_in_container": int(lg_exec[0]),
        "default_executor_width_min32_cpup4": int(lg_exec[1]),
        "dispatch": "sync graph nodes -> asyncio run_in_executor (default "
                    "ThreadPoolExecutor)",
    }
    report["decision"] = decision
    (OUT / "stage0_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({"same_token": same_token, "new_workers": new_workers,
                      "pool_size": decision["pool_size"],
                      "lg_executor_width": report["lg"][
                          "default_executor_width_min32_cpup4"]}))


if __name__ == "__main__":
    asyncio.run(main())
