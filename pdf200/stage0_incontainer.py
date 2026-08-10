"""Stage 0 client part — runs INSIDE the RR container via docker exec -i.

Connects 3 clients, all use() the same pipeline file with the same
project_id and use_existing=True; prints the three use results as JSON.
"""

import asyncio
import json
import uuid
from pathlib import Path

URI = "ws://127.0.0.1:5565/task/service"
OUT = Path("/work/pdf200")


async def main():
    from rocketride import RocketRideClient

    OUT.mkdir(parents=True, exist_ok=True)
    pipe = json.loads(Path("/work/benchmark_pdf.pipe").read_text())
    pipe["project_id"] = str(uuid.uuid4())
    pipe_path = OUT / "shared_pipeline.pipe"
    pipe_path.write_text(json.dumps(pipe))
    (OUT / "rr_pool_state.json").write_text(json.dumps(
        {"pipe_path": str(pipe_path), "project_id": pipe["project_id"]}))

    uses = []
    clients = []
    for i in range(3):
        c = RocketRideClient(uri=URI, auth="local-dev")
        await c.connect()
        used = await c.use(filepath=str(pipe_path), use_existing=True, ttl=7200)
        clients.append(c)
        uses.append({k: used.get(k) for k in
                     ("token", "id", "projectId", "provider")})
        print(json.dumps({"marker": f"AFTER_USE_{i+1}", "use": uses[-1]}),
              flush=True)
        await asyncio.sleep(2)  # give engine time to spawn workers if it will

    for c in clients:
        try:
            await c.disconnect()
        except Exception:
            pass
    print(json.dumps({"marker": "DONE", "uses": uses}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
