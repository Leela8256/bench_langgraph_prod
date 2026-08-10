"""HTTP layer for document-pdf-v1."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipelines.document_pdf.adapter import build_document_pdf_pipeline
from pipelines.mock import build_mock_pipeline
from service.app import create_app
from service.config import Settings

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PDF = FIX / "text_page.pdf"

pytestmark = pytest.mark.slow

BUILDERS = {
    "mock-v1": build_mock_pipeline,
    "document-pdf-v1": build_document_pdf_pipeline,
}


def settings_for(pipelines: str, tmp_path: Path) -> Settings:
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    return Settings.from_env(
        {"PIPELINES": pipelines, "TEMP_DIRECTORY": str(d), "MAX_UPLOAD_BYTES": "10000000"}
    )


@pytest.fixture(scope="module")
def pdf_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pdfapp")
    return create_app(settings=settings_for("document-pdf-v1", tmp), builders=BUILDERS)


def post_pdf(client, path=PDF, media_type="application/pdf", **form):
    with open(path, "rb") as fh:
        return client.post(
            "/v1/process/document-pdf-v1",
            files={"file": (path.name, fh.read(), media_type)},
            data=form,
        )


def test_happy_path_canonical_bytes_and_hash(pdf_app):
    with TestClient(pdf_app) as client:
        resp = post_pdf(client, request_id="pdf-1")
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Pipeline"] == "document-pdf-v1"
    assert resp.headers["X-Request-ID"] == "pdf-1"
    assert resp.headers["X-Output-SHA256"] == hashlib.sha256(resp.content).hexdigest()

    body = json.loads(resp.content)
    assert body["schema_version"] == 1
    assert body["input"]["media_type"] == "application/pdf"
    out = body["output"]
    assert out["chunks"] and out["vectors"]
    assert len(out["chunks"]) == len(out["vectors"])
    assert len(out["vectors"][0]) == 384
    # timings stay in headers only
    assert "timings_ns" not in body and "timings" not in out
    for stage in ("extract", "chunk", "embed", "assemble", "total"):
        assert f"{stage};dur=" in resp.headers["Server-Timing"]


def test_http_result_matches_direct_graph_result(pdf_app):
    """Correctness hierarchy: HTTP output == direct graph output, byte for byte."""
    import asyncio

    from pipelines.document_pdf.graph import build_pdf_graph
    from service.canonical import canonical_encode

    final = asyncio.run(
        build_pdf_graph().ainvoke({"source_path": str(PDF), "timings_ns": {}})
    )
    with TestClient(pdf_app) as client:
        resp = post_pdf(client)
    http_output = json.loads(resp.content)["output"]
    assert canonical_encode(http_output) == canonical_encode(final["result"])


def test_wrong_media_type_415(pdf_app):
    with TestClient(pdf_app) as client:
        resp = post_pdf(client, media_type="text/plain")
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


def test_missing_file_400(pdf_app):
    with TestClient(pdf_app) as client:
        resp = client.post("/v1/process/document-pdf-v1", data={"options": "{}"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "empty_input"


def test_corrupt_pdf_processing_failed_500(pdf_app):
    with TestClient(pdf_app) as client:
        resp = post_pdf(client, path=FIX / "corrupt.pdf")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "processing_failed"


def test_no_text_pdf_succeeds_with_zero_chunks(pdf_app):
    with TestClient(pdf_app) as client:
        resp = post_pdf(client, path=FIX / "no_text.pdf")
    assert resp.status_code == 200
    assert json.loads(resp.content)["output"] == {"chunks": [], "vectors": []}


def test_meta_reports_pinned_pypdf_and_model(pdf_app):
    with TestClient(pdf_app) as client:
        meta = client.get("/meta").json()
    wv = meta["workload_versions"]
    assert wv["pypdf"] == "6.15.0"
    assert wv["embedding"]["model_id"] == "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    assert wv["split"] == {"chunk_size": 4000, "chunk_overlap": 200, "length_function": "len"}
    assert meta["pipelines"] == ["document-pdf-v1"]


def test_both_pipelines_served_with_one_model_load(tmp_path):
    from workload.document import embed

    app = create_app(
        settings=settings_for("mock-v1,document-pdf-v1", tmp_path), builders=BUILDERS
    )
    with TestClient(app) as client:
        assert client.get("/meta").json()["pipelines"] == ["document-pdf-v1", "mock-v1"]
        assert client.get("/health/ready").status_code == 200
        assert post_pdf(client).status_code == 200
        mock = client.post(
            "/v1/process/mock-v1",
            files={"file": ("a.bin", b"hello", "application/octet-stream")},
        )
        assert mock.status_code == 200
    # shared module-level model, not a per-pipeline copy
    assert embed.is_loaded()
    assert embed.load_model() is embed.load_model()
