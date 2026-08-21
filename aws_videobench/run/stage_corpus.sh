#!/usr/bin/env bash
# ONE-TIME full-corpus staging, ON THE BOX — DATA_FLOW_PLAN.md §2 exactly:
# mirror ──► mux ──► S3, per meeting, DELETE-AS-YOU-GO (~400 MB peak disk,
# no corpus copy ever accumulates on EBS).
#
#   for each of the 170 meetings in corpus/sets/ami_full.txt:
#     (a) download <mtg>.Closeup1.avi + <mtg>.Mix-Headset.wav   (mirror, slow)
#     (b) ffmpeg stream-copy mux -> <mtg>.avi                    (bitexact)
#     (c) sha256 + upload to  $S3_CORPUS/<mtg>.avi
#     (d) delete all three local files
#   then corpus_manifest.json (durations from the EDA sweep + shas) -> S3.
#
# RESUMABLE: meetings already in S3 are skipped — safe to rerun after any
# interruption. Pre-seeding: videos already staged in corpus/ami30test are
# server-side copied (instant, no mirror traffic).
#
#   nohup bash run/stage_corpus.sh > ~/logs/stage.log 2>&1 < /dev/null &
set -euo pipefail
cd "$(dirname "$0")/.."

LIST=corpus/sets/ami_full.txt
DURS=corpus/sets/ami_full_durations.json
S3_CORPUS="${S3_CORPUS:-s3://rocketride-benchmark-data/leela/corpus/ami_full}"
S3_SEED="${S3_SEED:-s3://rocketride-benchmark-data/leela/corpus/ami30test}"
BASE="https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus"
CAM="Closeup1"
WORK="$HOME/stage_tmp"
SHAS="$HOME/stage_shas.jsonl"        # survives interruptions alongside S3 state
AWS_BIN="$(command -v aws || echo /usr/local/bin/aws)"
[ -x "$AWS_BIN" ] || AWS_BIN="$HOME/.local/bin/aws"
FFMPEG="$(command -v ffmpeg || echo "$HOME/bin/ffmpeg")"
[ -x "$FFMPEG" ] || { echo "FATAL: no ffmpeg (fetch_ami.sh installs the static build)" >&2; exit 1; }
mkdir -p "$WORK"; touch "$SHAS"

echo "== pre-seed from $S3_SEED (server-side copy, no downloads)"
"$AWS_BIN" s3 ls "$S3_SEED/" | awk '/\.avi$/{print $NF}' | while read -r f; do
  "$AWS_BIN" s3 cp "$S3_SEED/$f" "$S3_CORPUS/$f" --quiet 2>/dev/null || true
done

echo "== staging loop"
DONE_LIST="$WORK/.already_staged"
"$AWS_BIN" s3 ls "$S3_CORPUS/" | awk '/\.avi$/{print $NF}' > "$DONE_LIST"
total=$(grep -cv '^#' "$LIST")
n=0
grep -v '^#' "$LIST" | while read -r m; do
  n=$((n+1))
  if grep -qx "$m.avi" "$DONE_LIST"; then continue; fi
  vid="$WORK/$m.$CAM.avi"; wav="$WORK/$m.Mix-Headset.wav"; mux="$WORK/$m.avi"
  curl -fsSL --max-time 1800 "$BASE/$m/video/$m.$CAM.avi" -o "$vid"
  curl -fsSL --max-time 1800 "$BASE/$m/audio/$m.Mix-Headset.wav" -o "$wav"
  "$FFMPEG" -nostdin -loglevel error -y -i "$vid" -i "$wav" \
    -map 0:v:0 -map 1:a:0 -c copy \
    -map_metadata -1 -fflags +bitexact -flags:v +bitexact -flags:a +bitexact \
    "$mux"
  sha=$(sha256sum "$mux" | cut -d' ' -f1)
  "$AWS_BIN" s3 cp "$mux" "$S3_CORPUS/$m.avi" --quiet
  echo "{\"doc\": \"$m.avi\", \"sha256\": \"$sha\", \"bytes\": $(stat -c%s "$mux")}" >> "$SHAS"
  rm -f "$vid" "$wav" "$mux"
  echo "staged $m.avi ($n/$total)"
done

echo "== manifest"
python3 - "$DURS" "$SHAS" <<'PY' > "$WORK/corpus_manifest.json"
import json, sys
durs = json.load(open(sys.argv[1]))
shas = {}
for line in open(sys.argv[2]):
    r = json.loads(line)
    shas[r["doc"]] = {"sha256": r["sha256"], "bytes": r["bytes"]}
print(json.dumps({
    "corpus": "ami_full",
    "source": "groups.inf.ed.ac.uk/ami/AMICorpusMirror (mux: Closeup1 + Mix-Headset, bitexact)",
    "n_docs": len(durs),
    "selection_rule": "all usable AMI meetings sorted by ID (TS3003d excluded: no Closeup1)",
    "duration_s": durs,
    "total_hours": round(sum(durs.values()) / 3600, 2),
    "sha256": shas,
    "note": "docs pre-seeded from corpus/ami30test carry no sha entry here; "
            "their input_sha256 values exist in the videobench run records",
}, indent=1))
PY
"$AWS_BIN" s3 cp "$WORK/corpus_manifest.json" "$S3_CORPUS/corpus_manifest.json" --quiet

final=$("$AWS_BIN" s3 ls "$S3_CORPUS/" | grep -c '\.avi$')
echo "corpus staged: $final/$total videos at $S3_CORPUS"
rm -rf "$WORK"
[ "$final" -eq "$total" ]
