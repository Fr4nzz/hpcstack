# Monitoring jobs without holding a tool call open

## What the job writes

- `status.json` in the run folder, overwritten in place every few minutes and at each phase change:
  `phase`, `progress` (e.g. epoch 12/60 or 340/500 samples), `elapsed`, `eta`, last and best metric,
  `time`. Questions about progress or ETA are answered from this file, not estimated.
- The log, copied from node-local disk to the run folder every few minutes (overwrite, don't append new
  files).
- On exit: a final status (`done` or `failed` with the exit code) and checksums of the outputs.

## How the agent waits

Bad:

```bash
while true; do bjobs 1234; sleep 60; done      # blocks the session, never ends on its own
```

Good: one background watcher that polls every 60 s and exits on a terminal state, a long PEND or an error.

```bash
# LSF. UNTIL=RUN before a long handoff. Over ssh, put the ssh command inside the timeout.
J=1234; UNTIL=END; CAP=2700; PEND_S=1200   # background: set the tool's own timeout parameter >= CAP+180 s (e.g. 3000 s), not only a shell timeout; foreground (600 s): CAP=420 PEND_S=300
ts=$(date +%s); t0=${T0:-$ts}   # ts: this call (CAP). t0: first call (PEND_S); pass T0 on a re-run
while :; do
  rc=0; r=$(timeout 60 bjobs -noheader -o stat "$J" 2>&1) || rc=$?; s=${r##*$'\n'}   # survives set -e
  case $s in
    DONE|EXIT) echo "$s"; break ;;
    RUN) [[ $UNTIL == RUN ]] && { echo RUN; break; } ;;
    PEND) (( $(date +%s) - t0 > PEND_S )) && { echo PEND_LONG; break; } ;;
    UNKWN|PSUSP|USUSP|SSUSP|PROV|WAIT) ;;   # UNKWN = lost node contact, not EXIT: keep polling
    ZOMBI) echo ZOMBI; break ;;   # a kill LSF could not confirm: report once, read bacct
    *) if [[ $r == *"is not found"* ]]; then echo GONE; else echo "ERROR rc=$rc T0=$t0: $r"; fi; break ;;
  esac
  (( $(date +%s) - ts > CAP )) && { echo "CAP_REACHED last=$s T0=$t0"; break; }
  sleep 60
done
# SLURM, same shape
J=1234; UNTIL=END; CAP=2700; PEND_S=1200; ts=$(date +%s); t0=${T0:-$ts}
while :; do
  rc=0; r=$(timeout 60 sacct -n -X -P -j "$J" -o State 2>&1) || rc=$?; s=${r%%$'\n'*}; s=${s%% *}
  case $s in
    COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL) echo "$s"; break ;;
    RUNNING) [[ $UNTIL == RUN ]] && { echo RUN; break; } ;;
    PENDING) (( $(date +%s) - t0 > PEND_S )) && { echo PEND_LONG; break; } ;;
    *) echo "ERROR rc=$rc T0=$t0: $r"; break ;;   # empty reply or error is not a state
  esac
  (( $(date +%s) - ts > CAP )) && { echo "CAP_REACHED last=$s T0=$t0"; break; }
  sleep 60
done
```

What the output means:

- `DONE`, `EXIT`, or `GONE` (a confirmed "is not found": finished and purged) are news, not verdicts. Read
  `status.json`, the log and `bacct` (see "When it ends").
- `PEND_LONG`: report the pending reason from `bjobs -p -l` (`lsf.md`, "Why pending"). If the request is
  over-strict, fix it. Otherwise hand off (state file and job file, with the pending reason sent to whoever must
  act). Re-run the loop only inside the same foreground call, never as a new background loop. Never leave a
  pending job with no handoff.
- Pending jobs submitted through `pendlog submit` (`right-size-jobs`): run `pendlog started --out DIR` every
  10 to 15 min, or from the wait loop, so their start, PEND and pending reason are logged.
- `ZOMBI`: report once and read `bacct`. Don't re-run the loop on it.
- `CAP_REACHED`: the loop hit its own limit before the job changed state. Not news about the job: hand off
  (state file and job file for a watcher). Don't start another background loop: each restart is one more turn end,
  and coordinators that relay turn ends then wake their parent each time. A foreground loop may continue in the
  same turn with the `T0=` value it printed. `PEND_S` is
  below `CAP`, so a pending job always reaches `PEND_LONG` first.
- `ERROR` (timeout, authentication or transport failure, or an empty reply) is not a job state. The job stays
  tracked. Check authentication first (an expired certificate does not kill jobs), then run the loop again once
  (this retry is for a diagnosed query failure, not a re-arm).
- A `status.json` or checkpoint that stopped advancing while the job is RUN means a helper died or the job
  hangs.

**Short waits (under about 45 min): wait inside one tool call** with the loop above, using your harness's
background facility (a background command or monitor), or one foreground command within the tool timeout.
Never a series of `sleep` tool calls, repeated short polls, or re-armed loops. If an agent coordinator relays
every turn end to a parent chat (some do, whatever the message says), each re-arm costs the parent a wake: one
child woke its coordinator 12 times overnight by re-arming a 25-minute loop. Set the background tool's own timeout above the
loop's cap: a loop capped at 90 min inside a 30 min background slot is killed at 30 min. The loop's real
length is its cap plus one sleep step plus one query timeout. Keep that sum under the tool timeout: two loops
that slept 600 s before checking a 2 h cap were killed before they reported.

**Long waits (over about 45 min):** first run the loop with `UNTIL=RUN`, and hand off only a running job. A
watcher that wakes on ended jobs does not wake on PEND, so a job still pending past `PEND_S` (20 min) is reported with
its reason instead. Once it runs, write the job ids, expected outputs and the next step to a state file, then
let a model-free watcher wake the agent on a state change. Prefer that to one wake at the projected finish:
jobs end early or late, and a fixed-time wake left one finished result unread for almost 4 h.
Background commands in agent harnesses often time out near 2 h, and each wake after a prompt-cache expiry
re-reads the whole context at full price.

**Stragglers and long pends also need a wake.** A watcher that wakes only when jobs end stays silent while a
job runs far too long or never starts. Record an expected runtime for each job. A job still RUN at about 2x its
expected time, or 2x another run of the same step, is a straggler: check the host load (`lsload`, `bhosts`),
start one copy that excludes that host, keep whichever finishes first and kill the other by exact id. One eval
meant for 20 min ran 6.4 h on an overloaded host while three checks saw it "running". Likewise give a pending
job a limit (for example 3 h): at the limit, re-read the pending reason and the hosts that can take it, then
relax one thing or ask. One replicate pended 7.7 h with nobody looking.

**A wait lives in one session.** After a login expiry, a network outage, a provider error, or a move of the
agent to another account, thread or machine, its pending wait may be gone. Whoever made the change re-checks
every watched job at once (a purged job may need `bhist -n 10 -l <id>` on LSF) and re-arms a wait for each.
"It was only waiting on the cluster" is not a reason to skip this: one result sat unread for 6.6 h that way.
Never assume a watcher or a later poll will notice.

## When it ends

A terminal scheduler state is news, not a verdict. Check, in order:

1. Exit code and reason (`bjobs -l`, `bacct -l`, `sacct`).
2. The last 50 lines of the log and the whole `.err` file. Search for silent fallbacks: warnings, "falling
   back", "retrying on CPU", "skipped".
3. Output invariants: file exists, expected row or file count, checksum matches the receipt, the parser
   reads it.
4. A worker or wrapper job's DONE says nothing about its tasks. One wave ended DONE with 6 of 9 runs crashed
   while building their data. Read the per-task results file before reporting success.
5. Resource use for the ledger (`right-size-jobs`). If the stage passed about 1.5x its planned GPU-h or
   core-h, say so and why.

Report what you verified and what you could not.
