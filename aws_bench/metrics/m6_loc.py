"""M6 — lines of code, four layers per arm. Static; no run needed.

Count = non-blank, non-comment lines (pure Python, no cloc dependency).
Runnable:  python3 -m metrics.m6_loc
"""

import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

LAYERS: Dict[str, Dict[str, List[str]]] = {
    "langgraph": {
        "pipeline_definition": [
            "langgraph-fastapi/pipelines/document_pdf/graph.py",
            "langgraph-fastapi/pipelines/document_pdf/nodes.py",
            "langgraph-fastapi/pipelines/document_pdf/state.py",
            "langgraph-fastapi/pipelines/document_pdf/adapter.py",
        ],
        "compute_transforms": [
            "langgraph-fastapi/workload/document/extract.py",
            "langgraph-fastapi/workload/document/extract_pdf.py",
            "langgraph-fastapi/workload/document/split.py",
            "langgraph-fastapi/workload/document/embed.py",
        ],
        "serving_integration": [
            "langgraph-fastapi/service/app.py",
            "langgraph-fastapi/service/canonical.py",
            "langgraph-fastapi/service/config.py",
            "langgraph-fastapi/service/errors.py",
            "langgraph-fastapi/service/pipeline.py",
            "langgraph-fastapi/service/registry.py",
            "langgraph-fastapi/service/schemas.py",
            "langgraph-fastapi/service/upload.py",
            "langgraph-fastapi/Dockerfile",
        ],
        "client_harness": ["pdf200/lg_stepped.py"],
    },
    "rocketride": {
        "pipeline_definition": ["rocketride/benchmark_pdf.pipe"],
        "compute_transforms": [],  # engine-internal: product code, not user code
        "serving_integration": [
            "rocketride/Dockerfile",
            "rocketride/docker-entrypoint.sh",
        ],
        "client_harness": ["pdf200/rr_stepped.py"],
    },
}

COMMENT_PREFIXES = {".py": "#", ".sh": "#", "Dockerfile": "#"}


def count_loc(path: Path) -> int:
    prefix = COMMENT_PREFIXES.get(path.suffix or path.name)
    n = 0
    in_docstring = False
    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        if path.suffix == ".py":
            if in_docstring:
                if '"""' in s:
                    in_docstring = False
                continue
            if s.startswith('"""'):
                if not (s.endswith('"""') and len(s) > 3):
                    in_docstring = True
                continue
        if prefix and s.startswith(prefix):
            continue
        n += 1
    return n


def loc_report() -> Dict:
    report = {}
    for arm, layers in LAYERS.items():
        arm_out, total = {}, 0
        for layer, files in layers.items():
            per_file = {}
            for f in files:
                p = ROOT / f
                per_file[f] = count_loc(p) if p.exists() else "MISSING"
            layer_total = sum(v for v in per_file.values() if isinstance(v, int))
            arm_out[layer] = {"total": layer_total, "files": per_file}
            total += layer_total
        arm_out["arm_total"] = total
        report[arm] = arm_out
    return report


if __name__ == "__main__":
    print(json.dumps(loc_report(), indent=1))
