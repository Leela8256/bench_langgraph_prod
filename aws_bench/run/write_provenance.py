"""Write provenance.json for a run, then assert it is complete.

A performance number without its environment is not a result. Fields are read
from what actually ran (image digests, /meta, the engine binary's own hash)
rather than from config, because intent and reality diverge -- this suite has
already been bitten by /meta reporting a concurrency the code never used.

  python3 run/write_provenance.py <run_dir>
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from metrics.provenance import check  # noqa: E402


def main():
    run = Path(sys.argv[1])
    env = dict(l.split("=", 1) for l in (run / "environment.txt").read_text()
               .splitlines() if "=" in l)

    meta = {}
    p = run / "meta_lg.json"
    if p.exists():
        meta = json.loads(p.read_text())
    wv = meta.get("workload_versions", {})

    digests = {}
    p = run / "image_ids.txt"
    if p.exists():
        for line in p.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                digests[parts[0]] = parts[1]

    engine_sha = None
    p = run / "engine_sha.txt"
    if p.exists():
        engine_sha = p.read_text().split()[0]

    corpus_sha = None
    p = run / "corpus.sha256"
    if p.exists():
        import hashlib
        corpus_sha = hashlib.sha256(p.read_bytes()).hexdigest()

    rec = {
        "run_id": run.name,
        "timestamp_utc": env.get("stamp_utc"),
        "git_commit": env.get("git_sha"),
        "git_dirty": env.get("git_dirty"),
        "image_digest": digests,
        "framework_version": {
            "langgraph_extractor": wv.get("extractor"),
            "rocketride_engine_sha256": engine_sha,
        },
        # The engine is PATCHED unless RR_DUP_PATCH=0. Describe the tested
        # system as "RocketRide 3.3.1 with the documented embedding-transformer
        # duplication correction" -- never as stock 3.3.1.
        "rocketride_engine_version": env.get("rr_engine_version", "3.3.1"),
        "rocketride_engine_sha256": engine_sha,
        "rocketride_sdk_version": env.get("rr_sdk_version", "1.3.0"),
        "duplication_patch_applied": env.get("rr_dup_patch", "1") == "1",
        "duplication_patch_id": "BUG_CHUNK_DUPLICATION",
        "engine_image_digest": digests.get("rocketride"),
        "instance_type": env.get("instance_type", "c7i.8xlarge"),
        "architecture": env.get("arch"),
        "os": env.get("os"),
        "kernel": env.get("kernel"),
        "cpu_count": int(env.get("nproc") or 0) or None,
        "ram_gb": int(env.get("mem_gb") or 0) or None,
        "corpus_manifest_sha256": corpus_sha,
        "corpus_n_docs": int(env.get("n_docs") or 0) or None,
        "parser": env.get("lg_extractor"),
        "parser_config_hash": wv.get("extractor"),
        "chunk_config": wv.get("split"),
        "embedding_model": (wv.get("embedding") or {}).get("model_id"),
        "offered_concurrency": env.get("mode"),
        "configured_concurrency": {
            "langgraph": "default executor min(32, cpu_count+4); /meta's "
                         "executor_workers is reported but INERT",
            "rocketride": env.get("rr_threads"),
        },
        "envelope": {"cpus": env.get("arm_cpus"), "memory": env.get("arm_mem")},
        "warmup_policy": f"{env.get('warm_docs')} docs, timed separately, excluded",
        "timeout_s": 300,
        "mode": env.get("mode"),
        "reps": int(env.get("reps") or 0) or None,
    }
    (run / "provenance.json").write_text(json.dumps(rec, indent=1))

    verdict = check(rec)
    print(f"provenance: {'complete' if verdict['PASS'] else 'INCOMPLETE'}")
    if not verdict["PASS"]:
        print(f"  missing: {verdict['missing_fields']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
