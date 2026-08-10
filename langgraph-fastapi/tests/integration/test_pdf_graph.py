"""Direct compiled-graph invocation — no HTTP layer involved."""

import json
from pathlib import Path

import pytest

from pipelines.document_pdf.graph import build_pdf_graph
from service.canonical import canonical_encode

FIX = Path(__file__).resolve().parents[1] / "fixtures"
WARMUP = Path(__file__).resolve().parents[2] / "pipelines/document_pdf/fixtures/warmup.pdf"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def graph():
    return build_pdf_graph()


async def run(graph, pdf: Path):
    return await graph.ainvoke({"source_path": str(pdf), "timings_ns": {}})


@pytest.mark.anyio
async def test_fixture_pdf_end_to_end(graph):
    final = await run(graph, FIX / "text_page.pdf")
    result = final["result"]
    assert result["chunks"] and result["vectors"]
    assert len(result["chunks"]) > 1, "fixture must exercise the multi-chunk path"
    assert len(result["chunks"]) == len(result["vectors"])
    # Overlap is produced only where the splitter must break WITHIN a
    # separator group, so it is not a property of every boundary — but the
    # fixture is sized to guarantee at least one overlapping pair.
    overlaps = [
        a["end"] - b["start"]
        for a, b in zip(result["chunks"], result["chunks"][1:])
        if b["start"] < a["end"]
    ]
    assert overlaps, "fixture must exercise the overlapping-chunk path"
    assert all(0 < o <= 200 for o in overlaps), f"overlap must respect 200: {overlaps}"
    for c, v in zip(result["chunks"], result["vectors"]):
        assert set(c) == {"index", "text", "start", "end"}
        assert len(v) == 384
    assert set(final["timings_ns"]) == {"extract", "chunk", "embed", "assemble"}


@pytest.mark.anyio
async def test_graph_determinism_byte_identical(graph):
    a = await run(graph, FIX / "text_page.pdf")
    b = await run(graph, FIX / "text_page.pdf")
    assert canonical_encode(a["result"]) == canonical_encode(b["result"])


@pytest.mark.anyio
async def test_warmup_fixture_runs(graph):
    final = await run(graph, WARMUP)
    assert final["result"]["chunks"]
    assert len(final["result"]["vectors"][0]) == 384


@pytest.mark.anyio
async def test_pdf_with_no_extractable_text_is_not_an_error(graph):
    """Zero chunks, zero vectors, valid result — the census interprets it."""
    final = await run(graph, FIX / "no_text.pdf")
    assert final["result"] == {"chunks": [], "vectors": []}
    assert canonical_encode(final["result"]) == b'{"chunks":[],"vectors":[]}'


@pytest.mark.anyio
async def test_corrupt_pdf_propagates(graph):
    """The node lets pypdf's exception out; the server maps it."""
    with pytest.raises(Exception):
        await run(graph, FIX / "corrupt.pdf")


@pytest.mark.anyio
async def test_result_is_canonical_encodable(graph):
    """No NaN/Inf can reach the encoder from real vectors."""
    final = await run(graph, WARMUP)
    body = canonical_encode(final["result"])
    assert json.loads(body)["vectors"]
