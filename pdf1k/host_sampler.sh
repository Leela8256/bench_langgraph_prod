#!/bin/sh
# Host-level 1s sampler (macOS): CPU idle%, load average, memory, swap, disk.
# One JSON line per second to stdout.
while true; do
  TS=$(date +%s.%N 2>/dev/null || python3 -c 'import time;print(time.time())')
  LOAD=$(sysctl -n vm.loadavg | tr -d '{}' | awk '{print $1","$2","$3}')
  CPU=$(top -l 1 -n 0 2>/dev/null | awk -F'[:,%]' '/CPU usage/{gsub(/ /,"");print $2","$4","$6}')
  SWAP=$(sysctl -n vm.swapusage | awk '{print $3","$6}' | tr -d 'M')
  PAGEOUT=$(vm_stat | awk '/Pageouts/{gsub(/\./,"");print $2}')
  echo "{\"ts\":$TS,\"load\":[$LOAD],\"cpu_user_sys_idle\":[${CPU:-0,0,0}],\"swap_used_free_mb\":[${SWAP:-0,0}],\"pageouts\":${PAGEOUT:-0}}"
  sleep 1
done
