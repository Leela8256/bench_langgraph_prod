#!/usr/bin/env bash
# Get a run OFF the box. Runs ON the box.
#
# Constraint: no scp, no port forwarding, and (as of 2026-08-14) the laptop's
# SSO role has neither s3:ListBucket nor s3:GetObject on the bench bucket --
# so "upload to S3" alone does NOT mean you can retrieve it.
#
# This tries the routes in order of least dependence on anyone else:
#   1. tar+gzip the run and upload to S3            (durable, survives the box)
#   2. probe whether the INSTANCE role can read back (decides if 3 can work)
#   3. presign a download URL                        (no IAM grant needed on
#      your laptop -- the URL carries its own authorization; open in a browser)
#   4. --print: base64 to stdout, in chunks, with a checksum, so you can
#      copy-paste it out through the SSM session when all else fails
#
#   bash aws_run/box/exfil.sh <run_dir> [--print]
set -euo pipefail

RUN="${1:?usage: exfil.sh <run_dir> [--print]}"
PRINT="${2:-}"
[ -d "$RUN" ] || { echo "no such run dir: $RUN" >&2; exit 1; }

BUCKET="${BENCH_S3:-s3://rocketride-benchmark-data/leela}"
NAME="$(basename "$RUN")"
TGZ="/tmp/${NAME}.tgz"
say() { echo "[exfil $(date -u +%H:%M:%S)] $*"; }

# ------------------------------------------------------------------ 1. pack
tar czf "$TGZ" -C "$(dirname "$RUN")" "$NAME"
SHA="$(sha256sum "$TGZ" | cut -d' ' -f1)"
SIZE="$(stat -c %s "$TGZ")"
say "packed $TGZ  $((SIZE / 1024)) KB  sha256=$SHA"
say "VERIFY AFTER DOWNLOAD:  sha256sum ${NAME}.tgz   ->  $SHA"

# ------------------------------------------------------------------ 2. upload
KEY="$BUCKET/$NAME.tgz"
if aws sts get-caller-identity >/dev/null 2>&1; then
  if aws s3 cp "$TGZ" "$KEY" --only-show-errors; then
    say "uploaded -> $KEY"
  else
    say "WARNING: upload failed; the box's disk is the only copy"
  fi

  # Can the INSTANCE role read its own writes? Presigning is local signing --
  # it always "succeeds" -- but the URL only works if this role has GetObject.
  # Probing now turns a broken link into a known fact.
  if aws s3 cp "$KEY" - 2>/dev/null | head -c 1 >/dev/null; then
    say "instance role CAN read back -> presigned URL will work"
    URL="$(aws s3 presign "$KEY" --expires-in 604800 2>/dev/null || true)"
    if [ -n "$URL" ]; then
      echo
      echo "=================== DOWNLOAD URL (treat as a secret) ==================="
      echo "$URL"
      echo "======================================================================="
      echo "Open in a browser on your laptop. No IAM grant needed -- the URL"
      echo "carries its own authorization. It expires with this instance's"
      echo "credentials (often hours, not the full 7 days requested)."
      echo
    fi
  else
    say "instance role CANNOT read back (write-only) -> presign would 403"
    say "  => use --print, or get s3:GetObject added"
  fi
else
  say "no instance role; S3 unavailable"
fi

# ------------------------------------------------------------------ 3. print
if [ "$PRINT" = "--print" ]; then
  if [ "$SIZE" -gt 2097152 ]; then
    say "REFUSING to print: $((SIZE / 1024 / 1024)) MB is too big to paste reliably."
    say "Print just the derived report instead:  cat $RUN/report.txt"
    exit 1
  fi
  echo
  echo "=== BEGIN BASE64 ${NAME}.tgz sha256=$SHA ==="
  base64 -w 100 "$TGZ"
  echo "=== END BASE64 ==="
  echo
  echo "On your laptop, save the block between the markers as run.b64, then:"
  echo "  base64 -d run.b64 > ${NAME}.tgz"
  echo "  sha256sum ${NAME}.tgz     # must equal $SHA"
  echo "  tar xzf ${NAME}.tgz"
fi
