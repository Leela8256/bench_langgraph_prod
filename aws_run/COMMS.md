# How we talk to the box — communication model

The permission set allows exactly one interactive door and one data exit:

```
Mac ──(AWS API: ec2 start/stop, ssm start-session)──▶ AWS control plane
Mac ──(SSM session: keystrokes/stdin over TLS)──────▶ box shell (ssm-user)
box ──(aws s3 cp, instance role)────────────────────▶ S3   ← ONLY way results leave
box ──(git clone/pull over HTTPS)◀──────────────────  GitHub  ← ONLY way code arrives
```

**Explicitly impossible** (don't burn time trying): scp, SSH, SSH-over-SSM,
port forwarding (→ you can never curl the box's services from the Mac; every
health check and driver runs ON the box), ssm:SendCommand, CloudWatch reads.

## The three ways to run a command on the box

Tested in this order on day one; `box.sh` wraps all of them.

1. **Interactive shell** (for a human): `./box.sh shell`
   → `aws ssm start-session --target i-0bdc8b1e18f2a5348`. Full TTY.
   Idle timeout applies (default 20 min) — never leave a bare long run in one.

2. **One-shot piped session** (how Claude drives the box): `./box.sh run '<cmd>'`
   Pipes the command + `exit` into a normal session's stdin. Guaranteed to be
   permitted (same document as the interactive shell). Output includes prompt
   echo noise; the wrapper appends an `__RC=<n>` marker so the remote exit code
   survives the pipe.

3. **AWS-StartInteractiveCommand document** (cleaner, maybe denied):
   `./box.sh runx '<cmd>'` — runs one command, exits when it completes, clean
   output. The permission set may restrict session documents to the default
   shell; test once on day one. If it works, prefer it over `run`.

## Long-running benchmark phases (hours)

Never inside a live session — SSM disconnects/idle-timeouts would kill the run.
Pattern:

```
./box.sh launch gate50 'cd bench_langgraph_prod && ./run_gate50.sh'   # nohup + log
./box.sh tail gate50            # poll ~/logs/gate50.log any time later
./box.sh ps                     # what's still running
```

`launch` = `nohup bash -c '<cmd>' > ~/logs/<name>.log 2>&1 &` on the box.
Survives session drop, survives Mac sleep. Does NOT survive the box's
auto-stop — but a measured run keeps CPU >20% so the box stays up while it
matters; after the run finishes and CPU idles for 1h, the box stops itself
(disk + logs survive; `./box.sh start` resumes; exfil then).

## What Claude can and can't do through this

- CAN: start/stop/status the instance; run any command on the box via
  `box.sh run`/`runx` (each call ≤10 min — fine, long work goes through
  `launch` + `tail` polling); read results back as command output; drive the
  entire checklist end-to-end.
- CAN'T: complete the SSO browser login (human clicks required —
  `./box.sh login` opens it, you approve); transfer files directly (no scp —
  code via git, data via S3); reach box ports from the Mac.

## Day-one smoke sequence (the "jump in that instant" script)

```
./box.sh login                                   # browser SSO — human approves
./box.sh status                                  # instance state
./box.sh start                                   # boots + waits for SSM Online
./box.sh run 'whoami && uname -m && nproc'       # proves the command channel
./box.sh runx 'echo doc-allowed'                 # tests method 3 (may be denied)
./box.sh run 'aws sts get-caller-identity && aws s3 ls'   # instance role + bucket discovery
./box.sh run 'git clone https://github.com/Leela8256/bench_langgraph_prod.git'
./box.sh run 'cd bench_langgraph_prod && bash aws_run/preflight.sh'
```

~5 minutes from "PR merged" ping to a preflighted box.

## Open questions (answerable only after access)

- Is `AWS-StartInteractiveCommand` permitted? (decides `runx` vs `run`)
- Which S3 bucket can the instance role write — and can the LOCAL profile read
  the same bucket? (decides dataset upload path vs re-download)
- Is tmux on the box? (nice-to-have; nohup pattern needs nothing)
