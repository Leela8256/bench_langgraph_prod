import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from service.app import create_app


def post_file(client, data=b"hello world", pipeline="mock-v1", **form):
    return client.post(
        f"/v1/process/{pipeline}",
        files={"file": ("input.bin", data, "application/octet-stream")},
        data=form,
    )


def test_health_live(app):
    with TestClient(app) as client:
        resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


def test_ready_503_before_lifespan_200_after(app):
    # No context manager -> lifespan (warm-up) has not run.
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "server_not_ready"

    with TestClient(app) as client:
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}


def test_meta_shape(app, settings, temp_dir):
    with TestClient(app) as client:
        resp = client.get("/meta")
    assert resp.status_code == 200
    meta = resp.json()
    assert meta["pid"] == os.getpid()
    assert meta["ready"] is True
    assert meta["pipelines"] == ["mock-v1"]
    assert meta["uvicorn_workers"] == 1
    assert meta["executor_workers"] == 4
    assert meta["max_inflight_requests"] == 8
    assert meta["torch_threads"] == 1
    assert meta["torch_interop_threads"] == 1
    assert meta["max_upload_bytes"] == 10000
    assert meta["temp_directory"] == str(temp_dir)
    assert meta["env_pins"]["TOKENIZERS_PARALLELISM"] == "false"
    assert "architecture" in meta


def test_process_happy_path_exact_canonical_bytes(app):
    data = b"hello world"
    with TestClient(app) as client:
        resp = post_file(client, data, request_id="rid-42", options='{"k":"v"}')

    assert resp.status_code == 200

    # Independently computed expected canonical bytes.
    expected_envelope = {
        "schema_version": 1,
        "request_id": "rid-42",
        "pipeline": "mock-v1",
        "input": {
            "filename": "input.bin",
            "media_type": "application/octet-stream",
            "size_bytes": len(data),
        },
        "output": {
            "kind": "mock",
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "options_echo": {"k": "v"},
        },
    }
    expected_bytes = json.dumps(
        expected_envelope, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    assert resp.content == expected_bytes

    assert resp.headers["X-Request-ID"] == "rid-42"
    assert resp.headers["X-Pipeline"] == "mock-v1"
    assert (
        resp.headers["X-Output-SHA256"]
        == hashlib.sha256(resp.content).hexdigest()
    )
    assert "graph;dur=" in resp.headers["Server-Timing"]
    assert "total;dur=" in resp.headers["Server-Timing"]
    # Timings never in the body.
    assert "timings" not in resp.json()


def test_request_id_generated_when_absent(app):
    with TestClient(app) as client:
        resp = post_file(client)
    assert resp.status_code == 200
    rid = resp.headers["X-Request-ID"]
    assert len(rid) == 32
    assert resp.json()["request_id"] == rid


def test_unknown_pipeline_404(app):
    with TestClient(app) as client:
        resp = post_file(client, pipeline="nope-v1")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_pipeline"


def test_malformed_options_400(app):
    with TestClient(app) as client:
        resp = post_file(client, options="{not json")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_options"


def test_non_object_options_400(app):
    with TestClient(app) as client:
        resp = post_file(client, options="[1,2,3]")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_options"


def test_oversized_upload_413(app):
    with TestClient(app) as client:
        resp = post_file(client, data=b"x" * 10001)  # MAX_UPLOAD_BYTES=10000
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_empty_file_400(app):
    with TestClient(app) as client:
        resp = post_file(client, data=b"")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "empty_input"


def test_missing_file_400_from_prepare_input(app):
    """mock-v1 requires a file — and enforces that itself, not via a 422."""
    with TestClient(app) as client:
        resp = client.post("/v1/process/mock-v1", data={"options": "{}"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "empty_input"
    assert body["error"]["request_id"]


def test_missing_file_checked_after_pipeline_lookup(app):
    """Unknown pipeline wins over the per-pipeline file requirement."""
    with TestClient(app) as client:
        resp = client.post("/v1/process/nope-v1", data={"options": "{}"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_pipeline"


def test_temp_directory_empty_after_each_request(app, temp_dir):
    with TestClient(app) as client:
        for data in (b"ok data", b"x" * 10001, b""):
            post_file(client, data=data)
            assert list(temp_dir.iterdir()) == []


def test_warmup_failure_aborts_startup(settings):
    class FailingWarmup:
        name = "mock-v1"

        async def warmup(self):
            raise RuntimeError("model load failed")

        async def prepare_input(self, context):
            return {}

        async def ainvoke(self, state):
            return state

        async def extract_output(self, final_state):
            return {}

    app = create_app(settings=settings, builders={"mock-v1": lambda: FailingWarmup()})
    with pytest.raises(RuntimeError, match="model load failed"):
        with TestClient(app):
            pass


def test_unknown_configured_pipeline_aborts_startup(settings, builders):
    from service.config import Settings
    from service.registry import RegistryError

    bad_settings = Settings.from_env(
        {"PIPELINES": "does-not-exist", "TEMP_DIRECTORY": settings.temp_directory}
    )
    app = create_app(settings=bad_settings, builders=builders)
    with pytest.raises(RegistryError):
        with TestClient(app):
            pass
