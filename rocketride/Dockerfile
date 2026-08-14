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
    && curl -fsSL "$ENGINE_URL" -o /tmp/engine.tgz \
    && tar -xzf /tmp/engine.tgz -C "$ENGINE_DIR" \
    && rm /tmp/engine.tgz \
    && echo "${ENGINE_SHA256}  ${ENGINE_DIR}/engine" | sha256sum -c - \
    && chmod +x "$ENGINE_DIR/engine"

# Client SDK pinned to the version the probe was verified against.
RUN pip install --no-cache-dir "rocketride==1.3.0"

WORKDIR /work
COPY run_probe.py benchmark_pdf.pipe ./
COPY data/ ./data/

ENV ROCKETRIDE_URI=ws://127.0.0.1:5565/task/service \
    ROCKETRIDE_APIKEY=local-dev \
    RR_ENGINE_DIR=/opt/rocketride/engine \
    RR_PORT=5565

EXPOSE 5565

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default: run the engine as a long-lived service. Override with `probe` to
# run the Phase R capture instead.
CMD ["serve"]
