"""LangGraph video-detect service — the LangGraph arm of the video benchmark.

FastAPI shell around graph.py, mirroring the PDF arm's service contract:

    POST /process            multipart video file -> documents JSON
    GET  /health/ready       200 only after models are loaded AND a warmup
                             frame has been through the full graph
    GET  /meta               arm identity for provenance

Run:  uvicorn service:app --host 0.0.0.0 --port 8200
"""

import hashlib
import os
import tempfile
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from graph import build_video_graph

ARM = "langgraph-video-detect-v1"
_graph = None
_ready = False


def _warmup():
    """Load both models and push one synthetic frame through everything —
    a cold model must never be paid for inside a measured request."""
    global _ready
    from PIL import Image
    from workload.chunk import chunk_lines
    from workload.detect import detect_frame
    from workload.embed import embed_chunks
    line = detect_frame(Image.new("RGB", (352, 288)))
    embed_chunks(chunk_lines([line or "[]"]))
    _ready = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph
    _graph = build_video_graph()
    _warmup()
    yield


app = FastAPI(title=ARM, lifespan=lifespan)


@app.get("/health/ready")
def ready():
    if not _ready:
        return JSONResponse({"ready": False}, status_code=503)
    return {"ready": True}


@app.get("/meta")
def meta():
    from workload.chunk import CHUNK_OVERLAP, CHUNK_SIZE
    from workload.detect import THRESHOLD
    from workload.embed import MODEL_ID
    from workload.frames import INTERVAL_S
    return {"arm": ARM,
            "frame_interval_s": INTERVAL_S,
            "detector": {"model": "rfdetr-base", "threshold": THRESHOLD},
            "splitter": {"chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP},
            "embedding": {"model": MODEL_ID, "dim": 384}}


def _process_sync(path: str) -> dict:
    t0 = time.perf_counter()
    state = _graph.invoke({"video_path": path, "timings": {}})
    docs = state["documents"]
    return {
        "arm": ARM,
        "documents": docs,
        "n_frames": len(state["det_lines"]),
        "n_chunks": len(docs),
        "total_chars": sum(len(d["page_content"]) for d in docs),
        "output_sha256": hashlib.sha256(
            "".join(d["page_content"] for d in docs).encode()).hexdigest(),
        "timings": {**state["timings"],
                    "total_s": round(time.perf_counter() - t0, 3)},
    }


@app.post("/process")
async def process(file: UploadFile):
    if not _ready:
        raise HTTPException(503, "not warmed up")
    suffix = os.path.splitext(file.filename or "video")[1] or ".avi"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        while chunk := await file.read(1 << 22):
            tf.write(chunk)
        tmp = tf.name
    try:
        # CPU-bound graph in a worker thread so the event loop stays live.
        result = await anyio.to_thread.run_sync(_process_sync, tmp)
        result["filename"] = file.filename
        return result
    finally:
        os.unlink(tmp)
