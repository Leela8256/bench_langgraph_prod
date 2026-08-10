"""Offline ground truth for the PDF-1K run — computed OUTSIDE both frameworks.

Per doc: expected chunk text sha256s + offsets (pypdf 6.15.0 + RecursiveCharacterTextSplitter
pure defaults on text+'\\n' — identical to the LangGraph arm's pinned workload).
Plus: reference embedding vectors for a deterministic 20-doc sample (first chunk
of every 50th doc) and one fixed drift-fixture text.

Output: results/ground_truth/{per_doc.jsonl, sample_vectors.json, fixture.json}

Run: langgraph-fastapi/.venv/bin/python pdf1k/ground_truth.py
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "langgraph-fastapi"))

from workload.document.extract_pdf import extract_pdf, pypdf_version  # noqa: E402
from workload.document.split import effective_split_config, split_document  # noqa: E402

CORPUS = ROOT / "datasets" / "govdocs"
OUT = ROOT / "results" / "ground_truth"
OUT.mkdir(parents=True, exist_ok=True)

FIXTURE_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump."
)


def main() -> int:
    docs = sorted(CORPUS.glob("*.pdf"))
    print(f"ground truth over {len(docs)} docs; pypdf={pypdf_version()} "
          f"split={effective_split_config()}", flush=True)
    sample_texts = {}
    n_fail = 0
    with open(OUT / "per_doc.jsonl", "w") as fh:
        for i, pdf in enumerate(docs):
            rec = {"doc": pdf.name}
            try:
                text = extract_pdf(str(pdf))
                chunks = split_document(text)
                rec.update({
                    "ok": True,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "text_chars": len(text),
                    "n_chunks": len(chunks),
                    "chunk_sha256": [hashlib.sha256(c["text"].encode()).hexdigest()
                                     for c in chunks],
                    "offsets": [[c["start"], c["end"]] for c in chunks],
                })
                if i % 50 == 0 and chunks:
                    sample_texts[pdf.name] = chunks[0]["text"]
            except Exception as exc:
                n_fail += 1
                rec.update({"ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:200]})
            fh.write(json.dumps(rec) + "\n")
            if (i + 1) % 200 == 0:
                print(f"  {i+1}/{len(docs)}", flush=True)

    # Reference vectors: native-host model over sample chunk texts + fixture.
    from workload.document.embed import embed_chunks
    names = sorted(sample_texts)
    vecs = embed_chunks([sample_texts[n] for n in names])
    (OUT / "sample_vectors.json").write_text(json.dumps({
        "note": "first chunk text of every 50th doc, embedded native-host; "
                "compare cross-platform with allclose atol=1e-5 rtol=1e-4",
        "docs": {n: v for n, v in zip(names, vecs)},
    }))
    (OUT / "fixture.json").write_text(json.dumps({
        "text": FIXTURE_TEXT,
        "vector": embed_chunks([FIXTURE_TEXT])[0],
    }))
    print(f"DONE: {len(docs)-n_fail} ok, {n_fail} extraction failures "
          f"(those docs are excluded from byte gates, kept in run)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
