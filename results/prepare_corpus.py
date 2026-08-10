"""Build the 200-PDF Govdocs1 corpus, deterministically.

Selection rule (recorded in every manifest line):
  govdocs1 zip threads in ascending order starting at 000; within a thread,
  filenames ascending; keep *.pdf; drop zero-byte, non-%PDF-magic, or
  encrypted files (logged); take the first 1000 survivors.

Run with the LangGraph venv python (pypdf 6.15.0 — the same version the
LangGraph arm extracts with):
  langgraph-fastapi/.venv/bin/python results/prepare_corpus.py <zip> [<zip>...]
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "datasets" / "govdocs"
RESULTS = ROOT / "results"
TARGET = 1000
SELECTION_RULE = (
    "govdocs1 zip threads ascending from 000; filenames ascending within "
    "thread; *.pdf only; drop zero-byte / non-%PDF-magic / encrypted; "
    "first 1000 survivors"
)

OUT.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(exist_ok=True)

kept, dropped = [], []

for zip_path in sys.argv[1:]:
    if len(kept) >= TARGET:
        break
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith(".pdf"))
        print(f"{zip_path}: {len(names)} pdf entries")
        for name in names:
            if len(kept) >= TARGET:
                break
            data = zf.read(name)
            base = Path(name).name
            if len(data) == 0:
                dropped.append({"file": base, "reason": "zero-byte"})
                continue
            if not data.startswith(b"%PDF"):
                dropped.append({"file": base, "reason": "no %PDF magic"})
                continue
            dest = OUT / base
            dest.write_bytes(data)
            try:
                if PdfReader(str(dest)).is_encrypted:
                    dropped.append({"file": base, "reason": "encrypted"})
                    dest.unlink()
                    continue
            except Exception as exc:
                # Damaged-but-PDF-magic stays in: it is real-world input and
                # becomes a fail-soft data point, not a corpus exclusion.
                pass
            kept.append({
                "filename": base,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_zip": Path(zip_path).name,
                "selection_rule": SELECTION_RULE,
            })

with open(RESULTS / "manifest.jsonl", "w") as fh:
    for row in kept:
        fh.write(json.dumps(row) + "\n")
with open(RESULTS / "dropped.jsonl", "w") as fh:
    for row in dropped:
        fh.write(json.dumps(row) + "\n")

print(f"kept {len(kept)} / target {TARGET}; dropped {len(dropped)}")
for d in dropped[:20]:
    print("  dropped:", d)
if len(kept) < TARGET:
    print(f"WARNING: only {len(kept)} valid PDFs — feed another thread zip")
    sys.exit(1)
