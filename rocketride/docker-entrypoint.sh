#!/bin/sh
# serve  -> boot the engine in the foreground (default)
# probe  -> run the Phase R probe, which boots/stops its own engine
# <other> -> exec verbatim
set -eu

case "${1:-serve}" in
  serve)
    echo "[entrypoint] booting RocketRide engine on :${RR_PORT:-5565}"
    cd "${RR_ENGINE_DIR:-/opt/rocketride/engine}"
    exec ./engine "${RR_ENGINE_DIR:-/opt/rocketride/engine}/ai/eaas.py"
    ;;
  probe)
    echo "[entrypoint] running Phase R probe"
    cd /work
    exec python run_probe.py
    ;;
  *)
    exec "$@"
    ;;
esac
