#!/usr/bin/env bash
# box.sh — single local entrypoint for the AWS benchmark box.
# See COMMS.md for the communication model. Usage:
#   ./box.sh login                    SSO login (opens browser, human approves)
#   ./box.sh status                   instance state
#   ./box.sh start                    start instance, wait until SSM is Online
#   ./box.sh stop                     stop instance
#   ./box.sh shell                    interactive SSM shell (for humans)
#   ./box.sh run  '<cmd>'             one-shot command via stdin-piped session
#   ./box.sh runx '<cmd>'             one-shot via AWS-StartInteractiveCommand (test first)
#   ./box.sh launch <name> '<cmd>'    long run: nohup on box, log ~/logs/<name>.log
#   ./box.sh tail <name> [lines]      tail that log (default 50)
#   ./box.sh ps                       running launched jobs on the box
set -euo pipefail

PROFILE="${BOX_PROFILE:-leela}"
INSTANCE="${BOX_INSTANCE:-i-0bdc8b1e18f2a5348}"
REGION="${BOX_REGION:-us-east-1}"
A=(aws --profile "$PROFILE" --region "$REGION")

die() { echo "box.sh: $*" >&2; exit 1; }

state() {
  "${A[@]}" ec2 describe-instances --instance-ids "$INSTANCE" \
    --query 'Reservations[0].Instances[0].State.Name' --output text
}

ssm_online() {
  "${A[@]}" ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo none
}

# One-shot over the default shell document: pipe command + exit into stdin.
# Remote exit code is echoed as __RC=<n> and re-raised locally.
pipe_run() {
  local cmd="$1" out rc
  out=$({ sleep 2; printf '%s\n' "$cmd" 'echo "__RC=$?"' 'exit'; } \
        | "${A[@]}" ssm start-session --target "$INSTANCE" 2>&1) || true
  printf '%s\n' "$out"
  rc=$(printf '%s\n' "$out" | grep -o '__RC=[0-9]*' | tail -1 | cut -d= -f2)
  [ -n "${rc:-}" ] || die "no exit marker in session output — session may not have opened"
  return "$rc"
}

case "${1:-}" in
  login)
    exec aws sso login --profile "$PROFILE"
    ;;
  status)
    echo "instance: $INSTANCE  state: $(state)  ssm: $(ssm_online)"
    ;;
  start)
    s=$(state)
    if [ "$s" != "running" ]; then
      echo "starting ($s)..."
      "${A[@]}" ec2 start-instances --instance-ids "$INSTANCE" >/dev/null
      "${A[@]}" ec2 wait instance-running --instance-ids "$INSTANCE"
    fi
    echo -n "waiting for SSM agent"
    for _ in $(seq 1 30); do
      [ "$(ssm_online)" = "Online" ] && { echo " — Online"; exit 0; }
      echo -n "."; sleep 5
    done
    die "SSM agent not Online after 150s"
    ;;
  stop)
    "${A[@]}" ec2 stop-instances --instance-ids "$INSTANCE" >/dev/null
    echo "stop requested (disk survives; ./box.sh start to resume)"
    ;;
  shell)
    exec "${A[@]}" ssm start-session --target "$INSTANCE"
    ;;
  run)
    [ $# -ge 2 ] || die "usage: box.sh run '<cmd>'"
    pipe_run "$2"
    ;;
  runx)
    [ $# -ge 2 ] || die "usage: box.sh runx '<cmd>'"
    params=$(python3 -c 'import json,sys; print(json.dumps({"command":[sys.argv[1]]}))' "$2")
    exec "${A[@]}" ssm start-session --target "$INSTANCE" \
      --document-name AWS-StartInteractiveCommand --parameters "$params"
    ;;
  launch)
    [ $# -ge 3 ] || die "usage: box.sh launch <name> '<cmd>'"
    name="$2"; cmd="$3"
    q=$(printf '%q' "$cmd")
    pipe_run "mkdir -p ~/logs && nohup bash -c $q > ~/logs/$name.log 2>&1 & echo launched $name pid \$!"
    ;;
  tail)
    [ $# -ge 2 ] || die "usage: box.sh tail <name> [lines]"
    pipe_run "tail -n ${3:-50} ~/logs/$2.log"
    ;;
  ps)
    pipe_run "pgrep -af 'bash -c' || echo '(no launched jobs)'"
    ;;
  *)
    sed -n '3,14p' "$0"; exit 1
    ;;
esac
