"""Send ONE document to an ALREADY-RUNNING RocketRide engine and report.

Unlike run_probe.py this does not boot or stop an engine — it connects to the
one the container is already serving, which is the shape a benchmark driver
uses.

    docker exec prodbench-rocketride python /work/send_one.py /work/data/probe/sample.pdf
"""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE_SRC = HERE / "benchmark_pdf.pipe"
URI = os.environ.get("ROCKETRIDE_URI", "ws://127.0.0.1:5565/task/service")
APIKEY = os.environ.get("ROCKETRIDE_APIKEY", "local-dev")


def documents_from(result):
    if isinstance(result, dict):
        docs = result.get("documents")
        if isinstance(docs, list) and all(isinstance(d, dict) for d in docs):
            return docs
        for k in ("result", "data", "output"):
            if k in result:
                got = documents_from(result[k])
                if got:
                    return got
        return []
    if isinstance(result, list):
        out = []
        for item in result:
            out.extend(documents_from(item))
        return out
    return []


async def main(doc_path: str) -> int:
    from rocketride import RocketRideClient

    src = Path(doc_path).resolve()
    data = src.read_bytes()
    print(f"document      : {src}")
    print(f"bytes         : {len(data)}")
    print(f"sha256        : {hashlib.sha256(data).hexdigest()}")

    pipe = json.loads(PIPE_SRC.read_text())
    pipe["project_id"] = str(uuid.uuid4())
    stamped = Path("/tmp") / f"send_one_{pipe['project_id']}.pipe"
    stamped.write_text(json.dumps(pipe))

    client = RocketRideClient(uri=URI, auth=APIKEY)
    await client.connect()
    print(f"connected     : {client.is_connected()} -> {URI}")
    token = None
    try:
        used = await client.use(filepath=str(stamped))
        token = used.get("token")
        print(f"token         : {token}")

        t0 = time.perf_counter()
        up = await client.send_files([(str(src), {"doc_id": "one"})], token)
        wall = time.perf_counter() - t0

        docs = documents_from(up)
        texts = [d.get("page_content", "") for d in docs]
        vecs = [d.get("embedding") for d in docs]
        print(f"wall (shape)  : {wall:.3f}s   <- emulated, never reportable")
        print(f"chunks        : {len(docs)}")
        print(f"chunk lengths : {[len(t) for t in texts]}")
        print(f"total chars   : {sum(len(t) for t in texts)}")
        if vecs and vecs[0]:
            n = sum(x * x for x in vecs[0]) ** 0.5
            print(f"vector dim    : {len(vecs[0])}")
            print(f"vector L2norm : {n:.8f}")
            print(f"embed model   : {docs[0].get('embedding_model')}")
            print(f"first 4 vals  : {[round(x, 6) for x in vecs[0][:4]]}")
        print(f"concat sha256 : {hashlib.sha256(''.join(texts).encode()).hexdigest()}")
        return 0 if docs else 1
    finally:
        if token:
            try:
                await client.terminate(token)
            except Exception:
                pass
        await client.disconnect()
        stamped.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
