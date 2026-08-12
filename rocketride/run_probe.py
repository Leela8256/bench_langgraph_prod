"""Phase R probe — boots the local RocketRide engine, runs the PDF benchmark
pipe, and captures everything the probe report needs.

Rerunnable and self-contained: it boots the engine itself, waits for the DAP
port, runs every capture, then tears the engine down.

Run inside a linux/amd64 container that has the engine tree and venv_rr:

    docker run --rm --platform linux/amd64 \
      -v prodbench-rr:/bench -v "$PWD:/work" -w /work rrbench-dev:latest \
      /bench/rr-lg-v4/venv_rr/bin/python run_probe.py

Produces NO benchmark numbers — timings recorded here are shape-only.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "probe"
DATA = HERE / "data" / "probe"
ENGINE_DIR = Path(os.environ.get("RR_ENGINE_DIR", "/bench/rr-lg-v4/engine"))
PORT = int(os.environ.get("RR_PORT", "5565"))
URI = f"ws://127.0.0.1:{PORT}/task/service"
PIPE_SRC = HERE / "benchmark_pdf.pipe"
SAMPLE_PDF = DATA / "sample.pdf"
PARITY_TXT = DATA / "parity_fixture.txt"

OUT.mkdir(parents=True, exist_ok=True)
LOG_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Engine lifecycle
# --------------------------------------------------------------------------
class Engine:
    """Boot `./engine <ENGINE_DIR>/ai/eaas.py` and wait for the DAP port."""

    def __init__(self) -> None:
        self.proc = None

    def _port_open(self) -> bool:
        with contextlib.closing(socket.socket()) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", PORT)) == 0

    def start(self, wait_s: int = 600) -> None:
        env = {
            **os.environ,
            "ROCKETRIDE_URI": URI,
            "ROCKETRIDE_APIKEY": os.environ.get("ROCKETRIDE_APIKEY", "local-dev"),
            "PYTHONPATH": str(ENGINE_DIR),
        }
        params = ENGINE_DIR.parent / "rrbench.json"
        if params.exists():
            env["RRBENCH_PARAMS"] = str(params)
        cmd = ["./engine", str(ENGINE_DIR / "ai" / "eaas.py")]
        log(f"booting engine: {' '.join(cmd)} (cwd={ENGINE_DIR})")
        self.logf = open(OUT / "engine_boot.log", "wb")
        self.proc = subprocess.Popen(
            cmd, cwd=str(ENGINE_DIR), env=env,
            stdout=self.logf, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if self._port_open():
                log(f"engine up on {PORT} after {time.time() - t0:.1f}s")
                return
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"engine exited rc={self.proc.returncode}; see probe/engine_boot.log"
                )
            time.sleep(0.5)
        raise TimeoutError(f"engine port {PORT} not open after {wait_s}s")

    def stop(self) -> None:
        if not self.proc:
            return
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=20)
        if self.proc.poll() is None:
            with contextlib.suppress(Exception):
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.logf.close()
        log("engine stopped")


# --------------------------------------------------------------------------
# Pipe helpers
# --------------------------------------------------------------------------
def load_pipe(fresh_id: bool = True, inject: dict | None = None) -> dict:
    """Load the pipe, optionally stamping a fresh uuid4 and injecting config.

    `inject` maps component id -> dict merged into that component's
    config["default"] (used for the splitter-ignore experiment).
    """
    pipe = json.loads(PIPE_SRC.read_text())
    if fresh_id:
        pipe["project_id"] = str(uuid.uuid4())
    for cid, extra in (inject or {}).items():
        for comp in pipe["components"]:
            if comp["id"] == cid:
                comp["config"].setdefault("default", {}).update(extra)
    return pipe


def write_pipe(pipe: dict, name: str) -> Path:
    p = OUT / name
    p.write_text(json.dumps(pipe, indent=2))
    return p


def extract_documents(result) -> list[dict]:
    """Pull the documents list out of a pipeline result, shape-tolerantly.

    Observed live shape from send_files():
        [ {action, filepath, ..., result: {documents: [{embedding, text, ...}]}} ]
    """
    if result is None:
        return []
    if isinstance(result, dict):
        docs = result.get("documents")
        if isinstance(docs, list) and all(isinstance(d, dict) for d in docs):
            return docs
        for key in ("result", "data", "output"):
            if key in result:
                got = extract_documents(result[key])
                if got:
                    return got
        return []
    if isinstance(result, list):
        collected: list[dict] = []
        for item in result:
            collected.extend(extract_documents(item))
        return collected
    return []


def doc_text(d: dict) -> str:
    for k in ("text", "content", "page_content", "chunk"):
        v = d.get(k)
        if isinstance(v, str):
            return v
    return ""


def doc_vector(d: dict):
    for k in ("vector", "embedding", "vectors", "embeddings"):
        v = d.get(k)
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return v
        if isinstance(v, list) and v and isinstance(v[0], list):
            return v[0]
    return None


async def run_once(client, pipe: dict, files, label: str, trace: bool = True):
    """use() -> send_files() -> capture. Returns a capture dict."""
    from rocketride import RocketRideClient

    cap = {"label": label, "project_id": pipe["project_id"]}

    # validate() is advisory here: a failure is recorded, never fatal, so a
    # picky validator can't mask a pipeline the engine actually runs.
    try:
        val = await client.validate(pipeline=pipe)
        errs = val.get("errors") if isinstance(val, dict) else None
        cap["validation_errors"] = errs
        log(f"  [{label}] validate(): {'errors=' + json.dumps(errs)[:200] if errs else 'clean'}")
    except Exception as exc:
        cap["validation_errors"] = f"{type(exc).__name__}: {exc}"
        log(f"  [{label}] validate() raised: {exc}")

    # use(filepath=...) is the proven launch path (matches the SDK docs and the
    # prior benchmark driver); the stamped copy carries the fresh uuid4.
    stamped = write_pipe(pipe, f"_stamped_{label}.pipe")
    kwargs = {"filepath": str(stamped)}
    if trace:
        kwargs["pipelineTraceLevel"] = str(RocketRideClient.TRACE_SUCCESS)
    used = await client.use(**kwargs)
    token = used.get("token") if isinstance(used, dict) else None
    cap["use_result"] = used
    cap["token"] = token
    log(f"  [{label}] token={token}")

    try:
        status_after_use = await client.get_task_status(token)
        cap["status_after_use"] = status_after_use

        t0 = time.perf_counter()
        up = await client.send_files(files, token)
        cap["send_files_result"] = up
        cap["wall_s_shape_only"] = round(time.perf_counter() - t0, 3)

        cap["status_after_send"] = await client.get_task_status(token)

        docs = extract_documents(up)
        cap["n_documents"] = len(docs)
        cap["documents"] = docs
        log(f"  [{label}] documents returned: {len(docs)}")
    finally:
        with contextlib.suppress(Exception):
            await client.terminate(token)
    return cap


def summarize(cap: dict) -> dict:
    docs = cap.get("documents") or []
    texts = [doc_text(d) for d in docs]
    vecs = [doc_vector(d) for d in docs]
    v0 = next((v for v in vecs if v), None)
    s = {
        "label": cap["label"],
        "project_id": cap["project_id"],
        "n_chunks": len(texts),
        "chunk_lengths": [len(t) for t in texts],
        "chunk_sha256": [sha(t) for t in texts],
        "concat_text_sha256": sha("".join(texts)),
        "total_chars": sum(len(t) for t in texts),
        "vector_dim": len(v0) if v0 else None,
        "vector_first8": [round(float(x), 8) for x in v0[:8]] if v0 else None,
    }
    if v0:
        norm = sum(float(x) * float(x) for x in v0) ** 0.5
        s["vector_l2_norm"] = round(norm, 8)
        s["appears_l2_normalized"] = abs(norm - 1.0) < 1e-3
    return s


async def main() -> int:
    # NOTE: do NOT put ENGINE_DIR on sys.path — it shadows the SDK's
    # `rocketride` package with the engine's internal one.
    from rocketride import RocketRideClient

    os.environ.setdefault("ROCKETRIDE_URI", URI)
    os.environ.setdefault("ROCKETRIDE_APIKEY", "local-dev")

    report = {
        "uri": URI,
        "pdf_sha256": sha(SAMPLE_PDF.read_bytes()),
        "pdf_bytes": SAMPLE_PDF.stat().st_size,
        "parity_txt_sha256": sha(PARITY_TXT.read_bytes()),
        "parity_txt": PARITY_TXT.read_text(),
        "captures": {},
        "summaries": {},
    }

    client = RocketRideClient(uri=URI, auth=os.environ["ROCKETRIDE_APIKEY"])
    await client.connect()
    try:
        log(f"connected: {client.is_connected()}")
        with contextlib.suppress(Exception):
            report["server_info"] = await client.get_server_info()

        # 1. PDF run A
        pipe_a = load_pipe()
        write_pipe(pipe_a, "pipe_run_a.pipe")
        cap_a = await run_once(client, pipe_a, [(str(SAMPLE_PDF), {"doc_id": "probe-1"})], "pdf_run_a")

        # 2. PDF run B — fresh uuid4, same bytes: determinism gate
        pipe_b = load_pipe()
        write_pipe(pipe_b, "pipe_run_b.pipe")
        cap_b = await run_once(client, pipe_b, [(str(SAMPLE_PDF), {"doc_id": "probe-2"})], "pdf_run_b")

        # 3. Splitter-ignore experiment. The schema exposes NO chunk-size field
        #    (see PROBE_REPORT), so the handoff's strlen=512-vs-4000 test is run
        #    as: inject the undeclared field the handoff drafted and see whether
        #    the engine rejects it or silently ignores it.
        pipe_c = load_pipe(inject={"preprocessor_1": {"strlen": 512}})
        write_pipe(pipe_c, "pipe_run_c_strlen512.pipe")
        cap_c = await run_once(client, pipe_c, [(str(SAMPLE_PDF), {"doc_id": "probe-3"})], "pdf_run_c_strlen512")

        # 4. Embedding-parity fixture: plain text, skips PDF parsing
        pipe_d = load_pipe()
        write_pipe(pipe_d, "pipe_run_d_txt.pipe")
        cap_d = await run_once(client, pipe_d, [(str(PARITY_TXT), {"doc_id": "parity-1"})], "txt_parity")
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()

    for cap in (cap_a, cap_b, cap_c, cap_d):
        report["captures"][cap["label"]] = cap
        report["summaries"][cap["label"]] = summarize(cap)

    sa, sb, sc = (report["summaries"][k] for k in ("pdf_run_a", "pdf_run_b", "pdf_run_c_strlen512"))
    sd = report["summaries"]["txt_parity"]
    # A gate can only PASS on non-empty output — otherwise two failed runs
    # would "agree" and report determinism that was never demonstrated.
    produced = sa["n_chunks"] > 0 and sb["n_chunks"] > 0
    report["gates"] = {
        "produced_output": produced,
        "determinism_identical_chunks": bool(
            produced and sa["n_chunks"] == sb["n_chunks"]
            and sa["chunk_sha256"] == sb["chunk_sha256"]
        ),
        "determinism_detail": {
            "run_a_n": sa["n_chunks"], "run_b_n": sb["n_chunks"],
            "run_a": sa["chunk_sha256"], "run_b": sb["chunk_sha256"],
        },
        "strlen512_identical_to_default": bool(
            produced and sc["n_chunks"] > 0 and sa["chunk_sha256"] == sc["chunk_sha256"]
        ),
        "strlen512_note": (
            "identical => engine ignored the undeclared strlen field "
            "(the schema exposes no chunk-size field at all)"
        ),
        "parity_fixture_vector_dim": sd["vector_dim"],
        "parity_fixture_produced": sd["n_chunks"] > 0,
    }

    # Artifacts
    docs_a = cap_a.get("documents") or []
    text_a = "\n".join(doc_text(d) for d in docs_a)
    (OUT / "rr_extracted_text.txt").write_text(text_a)
    (OUT / "rr_chunks.json").write_text(json.dumps(
        [{"i": i, "sha256": sha(doc_text(d)), "len": len(doc_text(d)), "text": doc_text(d)}
         for i, d in enumerate(docs_a)], indent=2))
    report["rr_extracted_text_sha256"] = sha(text_a)
    report["rr_extracted_text_chars"] = len(text_a)

    docs_d = cap_d.get("documents") or []
    (OUT / "parity_vectors.json").write_text(json.dumps(
        {"text": PARITY_TXT.read_text(),
         "text_sha256": report["parity_txt_sha256"],
         "vectors": [doc_vector(d) for d in docs_d]}, indent=2))

    (OUT / "probe_capture.json").write_text(json.dumps(report, indent=2, default=str))
    (OUT / "probe_run.log").write_text("\n".join(LOG_LINES))

    log("=== GATES ===")
    log(json.dumps(report["gates"], indent=2))
    log(f"extracted chars: {report['rr_extracted_text_chars']}  sha={report['rr_extracted_text_sha256'][:16]}")
    for k, s in report["summaries"].items():
        log(f"{k}: chunks={s['n_chunks']} lens={s['chunk_lengths'][:6]} dim={s['vector_dim']} norm={s.get('vector_l2_norm')}")
    return 0


if __name__ == "__main__":
    eng = Engine()
    rc = 1
    try:
        eng.start()
        rc = asyncio.run(main())
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        (OUT / "probe_run.log").write_text("\n".join(LOG_LINES))
    finally:
        eng.stop()
    sys.exit(rc)
