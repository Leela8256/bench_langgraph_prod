import hashlib
from pathlib import Path

import pytest

from workload.document.embed import EMBED_DIM, MODEL_ID, embed_chunks, model_info
from workload.document.extract_pdf import extract_pdf, pypdf_version
from workload.document.split import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    effective_split_config,
    split_document,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"
WARMUP = Path(__file__).resolve().parents[2] / "pipelines/document_pdf/fixtures/warmup.pdf"


# --- extraction ------------------------------------------------------------
def test_extract_pdf_nonempty_and_deterministic():
    a = extract_pdf(str(FIX / "text_page.pdf"))
    b = extract_pdf(str(FIX / "text_page.pdf"))
    assert a.strip()
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


def test_extract_pdf_warmup_fixture():
    assert "warmup fixture" in extract_pdf(str(WARMUP))


def test_extract_pdf_no_text_page_yields_empty():
    """A valid PDF with no text operators extracts to '' — not an error."""
    assert extract_pdf(str(FIX / "no_text.pdf")).strip() == ""


def test_extract_pdf_corrupt_raises():
    with pytest.raises(Exception):
        extract_pdf(str(FIX / "corrupt.pdf"))


def test_pypdf_version_is_the_pinned_one():
    assert pypdf_version() == "6.15.0"


# --- splitting -------------------------------------------------------------
def test_effective_split_config_is_pure_library_defaults():
    cfg = effective_split_config()
    assert cfg["chunk_size"] == CHUNK_SIZE == 4000
    assert cfg["chunk_overlap"] == CHUNK_OVERLAP == 200
    assert cfg["length_function"] == "len"


def test_split_applies_to_text_plus_newline():
    text = "abc"
    chunks = split_document(text)
    assert chunks and chunks[0]["text"].startswith("abc")


def test_split_chunk_record_shape_and_offsets():
    text = "\n".join(f"line {i} " + "word " * 40 for i in range(60))
    chunks = split_document(text)
    assert len(chunks) > 1, "fixture must exceed one chunk"
    for i, c in enumerate(chunks):
        assert set(c) == {"index", "text", "start", "end"}
        assert c["index"] == i
        assert c["end"] - c["start"] == len(c["text"])
        assert len(c["text"]) <= CHUNK_SIZE
    starts = [c["start"] for c in chunks]
    assert starts == sorted(starts), "offsets must be monotonic"


def test_split_empty_text_yields_zero_chunks():
    assert split_document("") == []


# --- embedding -------------------------------------------------------------
def test_model_info_is_pinned():
    info = model_info()
    assert info["model_id"] == MODEL_ID == "sentence-transformers/multi-qa-MiniLM-L6-cos-v1"
    assert info["device"] == "cpu"
    assert info["dim"] == 384
    assert info["normalize"] is True


def test_embed_empty_list_short_circuits():
    assert embed_chunks([]) == []


@pytest.mark.slow
def test_embed_shape_normalized_and_plain_floats():
    vecs = embed_chunks(["hello world", "second chunk"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == EMBED_DIM == 384
        assert all(type(x) is float for x in v), "must be Python floats, not numpy"
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-4, f"expected L2-normalized, got {norm}"


@pytest.mark.slow
def test_embed_deterministic():
    assert embed_chunks(["repeatable text"]) == embed_chunks(["repeatable text"])
