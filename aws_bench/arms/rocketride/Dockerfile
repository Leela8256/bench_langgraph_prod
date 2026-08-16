# RocketRide arm — self-hosted engine, no api.rocketride.ai, no network at run.
#
# The engine ships linux-x64 only, which is why BOTH arms are pinned to
# linux/amd64. On an arm64 Mac this runs under emulation: correctness only,
# never timings.
FROM --platform=linux/amd64 python:3.12-slim-bookworm

# libc++ is a hard runtime dependency of the C++ engine binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libc++1 libc++abi1 procps curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV ENGINE_DIR=/opt/rocketride/engine \
    ROCKETRIDE_PORT=5565 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Pinned release + checksum. A silent engine swap would invalidate every
# captured artifact, so a mismatch must fail the build.
#
# ENGINE_SHA256 is the hash of the EXTRACTED `engine` BINARY, not of the
# tarball — that is what the prior benchmark harness pins, and it is the
# artifact that actually determines behavior. The tarball is flat (ai/,
# engine, lib/, nodes/, ...), so do NOT strip components.
#
# 3.3.1 (was 3.2.1): the Mac cross-arm validation — chunk hashes matching the
# LG-tika arm byte-exactly — was done against native 3.3.1. Building 3.2.1 here
# would mean the box engine is not the engine that work validated. SHA is of
# the extracted linux-x64 `engine` binary, verified 2026-08-14.
ARG ENGINE_VERSION=3.3.1
ARG ENGINE_URL=https://github.com/rocketride-org/rocketride-server/releases/download/server-v3.3.1/rocketride-server-v3.3.1-linux-x64.tar.gz
ARG ENGINE_SHA256=95768e2640df2d34dd6dfea2e456f36da03ad80b091f9d057c116dfe748d9747

RUN mkdir -p "$ENGINE_DIR" \
    && curl -fsSL --retry 3 "$ENGINE_URL" -o /tmp/engine.tgz \
    && tar -xzf /tmp/engine.tgz -C "$ENGINE_DIR" \
    && rm /tmp/engine.tgz \
    # Some release tarballs nest a top-level dir; flatten if so (from Ansh's
    # image). Runs before the checksum so the hash is always taken at the
    # same path regardless of archive layout.
    && if [ ! -f "$ENGINE_DIR/engine" ]; then \
         inner="$(find "$ENGINE_DIR" -maxdepth 2 -name engine -type f | head -1)"; \
         [ -n "$inner" ] && mv "$(dirname "$inner")"/. "$ENGINE_DIR"/ 2>/dev/null || true; \
       fi \
    && test -f "$ENGINE_DIR/engine" && test -f "$ENGINE_DIR/ai/eaas.py" \
    && echo "${ENGINE_SHA256}  ${ENGINE_DIR}/engine" | sha256sum -c - \
    && chmod +x "$ENGINE_DIR/engine"

# ---------------------------------------------------------------------------
# WORKAROUND for a RocketRide engine bug (present in 3.3.0 AND 3.3.1).
#
# ai/common/models/gliner/requirements_gliner.txt pins:
#     onnxruntime-gpu==1.20.1 ; platform_system != 'Darwin'
#     onnxruntime==1.20.1     ; platform_system == 'Darwin'
#
# onnxruntime-gpu 1.20.1 WAS NEVER PUBLISHED to PyPI (1.20.0 and 1.20.2 exist);
# onnxruntime 1.20.1 does exist. So the Darwin branch resolves and the Linux
# branch cannot. The engine compiles ALL requirements files at boot, so this
# unsatisfiable pin -- in an OPT-IN NER feature this benchmark never loads --
# makes the engine fail to start on ANY Linux host:
#     Failed to compile constraints: no solution found ... onnxruntime-gpu==1.20.1
#
# Bumping only that pin to the nearest published patch. Nothing else is
# touched, and the pipeline under test never imports GLiNER. Remove once
# upstream fixes it. See aws_run/FINDING_rr_linux_boot.md.
# The pin appears in FIVE requirements files, across BOTH ai/ and nodes/:
#   ai/common/models/{audio/requirements_whisper,vision/requirements_pose,
#                     gliner/requirements_gliner}.txt
#   nodes/{audio_transcribe,anonymize}/requirements.txt
# All must move together -- patching only some produces a WORSE failure,
# "you require ==1.20.1 and ==1.20.2", because constraint compilation is
# global across every requirements file in the tree.
# Scope is every *.txt (matching Ansh's image), not just requirements*.txt, so
# a pin in any constraints-style file is caught too. find -exec rather than
# `grep -rl | xargs sed`: once upstream fixes the pin, grep matches nothing and
# bare xargs would run sed with no file, blocking on stdin and hanging the build.
RUN find "$ENGINE_DIR" -name '*.txt' -exec \
      sed -i 's/onnxruntime-gpu==1\.20\.1/onnxruntime-gpu==1.20.2/g' {} + \
    && echo "--- onnxruntime-gpu pins after patch (must all be 1.20.2):" \
    && (grep -rn "onnxruntime-gpu==" "$ENGINE_DIR" --include='*.txt' || true) \
    && ! grep -rq "onnxruntime-gpu==1\.20\.1" "$ENGINE_DIR" --include='*.txt'
# ---------------------------------------------------------------------------

# Client SDK pinned to the version the probe was verified against.
RUN pip install --no-cache-dir "rocketride==1.3.0"

WORKDIR /work
COPY benchmark_pdf.pipe ./
COPY data/ ./data/

ENV ROCKETRIDE_URI=ws://127.0.0.1:5565/task/service \
    ROCKETRIDE_APIKEY=local-dev \
    RR_ENGINE_DIR=/opt/rocketride/engine \
    RR_PORT=5565

EXPOSE 5565

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# The engine runs as a long-lived service; the driver connects to it.
CMD ["serve"]
