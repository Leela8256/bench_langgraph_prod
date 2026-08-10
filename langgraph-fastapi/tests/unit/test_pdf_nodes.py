"""Node wrappers: each returns ONLY its own update plus its timing key."""

from pathlib import Path

import pytest

from pipelines.document_pdf.nodes import (
    assemble_node,
    chunk_node,
    embed_node,
    extract_node,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_extract_node_update_only():
    out = extract_node({"source_path": str(FIX / "text_page.pdf"), "timings_ns": {}})
    assert set(out) == {"text", "timings_ns"}
    assert out["text"].strip()
    assert "extract" in out["timings_ns"] and out["timings_ns"]["extract"] > 0


def test_chunk_node_update_only():
    out = chunk_node({"text": "word " * 3000, "timings_ns": {"extract": 1}})
    assert set(out) == {"chunks", "timings_ns"}
    assert len(out["chunks"]) > 1
    # accumulates rather than replaces
    assert out["timings_ns"]["extract"] == 1
    assert "chunk" in out["timings_ns"]


@pytest.mark.slow
def test_embed_node_update_only():
    chunks = [{"index": 0, "text": "alpha", "start": 0, "end": 5}]
    out = embed_node({"chunks": chunks, "timings_ns": {}})
    assert set(out) == {"vectors", "timings_ns"}
    assert len(out["vectors"]) == 1 and len(out["vectors"][0]) == 384
    assert "embed" in out["timings_ns"]


def test_assemble_node_passes_through_verbatim():
    chunks = [{"index": 0, "text": "a", "start": 0, "end": 1}]
    vectors = [[0.1234567890123456] * 384]
    out = assemble_node({"chunks": chunks, "vectors": vectors, "timings_ns": {}})
    assert set(out) == {"result", "timings_ns"}
    assert out["result"]["chunks"] is chunks
    assert out["result"]["vectors"] is vectors
    # no rounding
    assert out["result"]["vectors"][0][0] == 0.1234567890123456
    assert "assemble" in out["timings_ns"]
