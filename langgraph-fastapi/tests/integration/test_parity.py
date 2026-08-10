"""Parity against Phase F reference artifacts, plus the cross-arm embedding
fixture.

The gd100 parity tests SKIP LOUDLY when GD100_REFERENCE_DIR is unset or
missing — a silent skip would let a green suite imply parity that was never
checked.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from pipelines.document_pdf.graph import build_pdf_graph
from workload.document.embed import embed_chunks
from workload.document.extract_pdf import extract_pdf
from workload.document.split import split_document

REF_ENV = "GD100_REFERENCE_DIR"
REF_DIR = os.environ.get(REF_ENV)
MAX_DOCS = 10

ATOL = 1e-6
RTOL = 1e-5

pytestmark = pytest.mark.slow

_reason = (
    f"LOUD SKIP: {REF_ENV} is not set — Phase F reference artifacts are absent, "
    f"so gd100 parity was NOT verified. This suite being green does NOT mean "
    f"parity holds. Set {REF_ENV} to the Phase F output directory to enable."
)
if REF_DIR and not Path(REF_DIR).is_dir():
    _reason = (
        f"LOUD SKIP: {REF_ENV}={REF_DIR!r} does not exist — gd100 parity NOT verified."
    )
    REF_DIR = None

requires_reference = pytest.mark.skipif(not REF_DIR, reason=_reason)


def _reference_docs():
    refs = sorted(Path(REF_DIR).glob("*.json"))[:MAX_DOCS]
    if not refs:
        pytest.fail(f"{REF_ENV}={REF_DIR} contains no *.json reference artifacts")
    return refs


@requires_reference
@pytest.mark.parametrize("ref_path", _reference_docs() if REF_DIR else [])
def test_gd100_document_parity(ref_path):
    ref = json.loads(Path(ref_path).read_text())
    pdf = ref.get("source_path") or str(Path(REF_DIR) / ref["filename"])

    text = extract_pdf(pdf)
    assert hashlib.sha256(text.encode()).hexdigest() == ref["text_sha256"], (
        f"extracted text differs from reference for {ref_path.name}"
    )

    chunks = split_document(text)
    assert len(chunks) == len(ref["chunks"])
    for got, want in zip(chunks, ref["chunks"]):
        assert got["index"] == want["index"]
        assert got["start"] == want["start"] and got["end"] == want["end"]
        assert hashlib.sha256(got["text"].encode()).hexdigest() == want["sha256"]

    vectors = embed_chunks([c["text"] for c in chunks])
    assert len(vectors) == len(ref["vectors"])
    for got, want in zip(vectors, ref["vectors"]):
        assert len(got) == len(want)
        for a, b in zip(got, want):
            assert abs(a - b) <= ATOL + RTOL * abs(b), (
                f"vector mismatch beyond atol={ATOL} rtol={RTOL}"
            )


@requires_reference
def test_http_result_equals_direct_graph_result_on_reference_doc(tmp_path):
    """Final step of the correctness hierarchy, on a real reference document."""
    import asyncio

    from service.canonical import canonical_encode

    ref = json.loads(_reference_docs()[0].read_text())
    pdf = ref.get("source_path") or str(Path(REF_DIR) / ref["filename"])
    final = asyncio.run(build_pdf_graph().ainvoke({"source_path": pdf, "timings_ns": {}}))
    assert canonical_encode(final["result"])


def test_write_embedding_parity_fixture_vectors():
    """Embed the Phase R plain-text fixture and save vectors for cross-arm
    allclose against RocketRide's. Always runs — it is a producer, not a gate.
    """
    rr_fixture = (
        Path(__file__).resolve().parents[3] / "rocketride/data/probe/parity_fixture.txt"
    )
    if not rr_fixture.exists():
        pytest.skip(f"LOUD SKIP: Phase R fixture missing at {rr_fixture}")

    text = rr_fixture.read_text()
    vectors = embed_chunks([text])
    out = Path(__file__).resolve().parents[2] / "probe" / "lg_parity_vectors.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "model_id": "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
        "atol": ATOL,
        "rtol": RTOL,
        "vectors": vectors,
    }, indent=2))

    assert len(vectors) == 1 and len(vectors[0]) == 384
    norm = sum(x * x for x in vectors[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-4
