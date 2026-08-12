"""Generate the committed PDF fixtures. Deterministic, stdlib only.

Run from the repo root:  python3 tests/fixtures/make_fixtures.py

Outputs (all committed, hashes recorded in tests/fixtures/FIXTURES.md):
  pipelines/document_pdf/fixtures/warmup.pdf  tiny, used by adapter.warmup()
  tests/fixtures/text_page.pdf                multi-line single page
  tests/fixtures/no_text.pdf                  valid PDF, page with no text ops
  tests/fixtures/corrupt.pdf                  truncated -> pypdf must raise
"""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def esc(s: str) -> bytes:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").encode("ascii")


def build_pdf(pages: list[list[str]]) -> bytes:
    """pages: list of pages, each a list of text lines (empty list = no text)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add(b"")  # placeholder

    page_ids = []
    for lines in pages:
        if lines:
            content = b"BT\n/F1 11 Tf\n16 TL\n62 742 Td\n"
            for ln in lines:
                content += b"(" + esc(ln) + b") Tj T*\n"
            content += b"ET"
        else:
            content = b""  # valid page, zero text operators
        cid = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, font_id, cid)
        ))

    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(page_ids), kids,
    )
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (len(objects) + 1)
    for i, body in enumerate(objects, start=1):
        offsets[i] = len(out)
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"

    xref_at = len(out)
    n = len(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for i in range(1, n):
        out += b"%010d 00000 n \n" % offsets[i]
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        n, catalog_id, xref_at,
    )
    return bytes(out)


WARMUP_LINES = [
    "RocketRide benchmark warmup fixture.",
    "This page exists so pipeline warmup exercises the full graph.",
    "Extract, chunk, embed, assemble.",
]

# ~9000 chars so the 4000/200 splitter produces multiple overlapping chunks.
WORDS = (
    "pipeline document extraction benchmark vector embedding chunk latency "
    "throughput deterministic parser corpus token overlap splitter engine"
).split()
# Lines long enough that ONE page exceeds 4000 chars. pypdf emits a blank line
# between pages, and RecursiveCharacterTextSplitter splits on "\n\n" first — so
# if a page fit under chunk_size, each page would become one chunk and overlap
# would never be exercised. Oversized pages force intra-page splitting.
TEXT_PAGE_LINES = [
    "L{:02d} {}".format(i + 1, " ".join(WORDS[(i * 5 + j) % len(WORDS)] for j in range(14)))
    for i in range(45)
]


def write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f"{path.relative_to(ROOT)}  {len(data)} bytes  sha256={digest}")
    return digest


if __name__ == "__main__":
    write(ROOT / "pipelines/document_pdf/fixtures/warmup.pdf", build_pdf([WARMUP_LINES]))
    # 3 pages so extraction exceeds 4000 chars and the splitter must produce
    # MULTIPLE overlapping chunks — otherwise the end-to-end test would only
    # ever see the single-chunk path.
    write(ROOT / "tests/fixtures/text_page.pdf", build_pdf([TEXT_PAGE_LINES] * 3))
    write(ROOT / "tests/fixtures/no_text.pdf", build_pdf([[]]))
    # Truncated mid-body: header present, xref destroyed -> pypdf must raise.
    write(ROOT / "tests/fixtures/corrupt.pdf", build_pdf([WARMUP_LINES])[:180])
