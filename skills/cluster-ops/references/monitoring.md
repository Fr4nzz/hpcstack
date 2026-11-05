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
J=1234; UNTIL=END
t0=$(date +%s)
while :; do
  rc=0; r=$(timeout 60 bjobs -noheader -o stat "$J" 2>&1) || rc=$?; s=${r##*$'\n'}   # survives set -e
  case $s in
    DONE|EXIT) echo "$s"; break ;;
    RUN) [[ $UNTIL == RUN ]] && { echo RUN; break; } ;;
    PEND) (( $(date +%s) - t0 > 1800 )) && { echo PEND_30MIN; break; } ;;
    UNKWN|PSUSP|USUSP|SSUSP|PROV|WAIT) ;;   # UNKWN = lost node contact, not EXIT: keep polling
    ZOMBI) echo ZOMBI; break ;;   # a kill LSF could not confirm: report once, read bacct
    *) if [[ $r == *"is not found"* ]]; then echo GONE; else echo "ERROR rc=$rc: $r"; fi; break ;;
  esac
  sleep 60
done
# SLURM, same shape
J=1234; UNTIL=END; t0=$(date +%s)
while :; do
  rc=0; r=$(timeout 60 sacct -n -X -P -j "$J" -o State 2>&1) || rc=$?; s=${r%%$'\n'*}; s=${s%% *}
  case $s in
    COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL) echo "$s"; break ;;
    RUNNING) [[ $UNTIL == RUN ]] && { echo RUN; break; } ;;
    PENDING) (( $(date +%s) - t0 > 1800 )) && { echo PEND_30MIN; break; } ;;
    *) echo "ERROR rc=$rc: $r"; break ;;   # empty reply or error is not a state
  esac
  sleep 60
done
```

What the output means:

- `DONE`, `EXIT`, or `GONE` (a confirmed "is not found": finished and purged) are news, not verdicts. Read
  `status.json`, the log and `bacct` (see "When it ends").
- `PEND_30MIN`: report the pending reason from `bjobs -p -l` (`lsf.md`, "Why pending"). If the request is
  over-strict, fix it. Otherwise hand off anyway (state file and job file, with the reason in the handoff) or
  re-run the `UNTIL=RUN` loop and say so. Never leave a pending job with no handoff.
- `ZOMBI`: report once and read `bacct`. Don't re-run the loop on it.
- `ERROR` (timeout, authentication or transport failure, or an empty reply) is not a job state. The job stays
  tracked. Check authentication first (an expired certificate does not kill jobs), then run the loop again.
- A `status.json` or checkpoint that stopped advancing while the job is RUN means a helper died or the job
  hangs.

**Short waits (under about 1 h): wait inside one tool call** with the loop above, using your harness's
background facility (a background command or monitor), or one foreground command within the tool timeout.
Never a series of `sleep` tool calls or repeated short polls. Set the background tool's own timeout above the
loop's cap: a loop capped at 90 min inside a 30 min background slot is killed at 30 min.

**Long waits (over about 1 h):** first run the loop with `UNTIL=RUN`, and hand off only a running job. A
watcher that wakes on ended jobs does not wake on PEND, so a job still pending after 30 min is reported with
its reason instead. Once it runs, write the job ids, expected outputs and the next step to a state file, then
let a model-free watcher wake the agent on a state change. Prefer that to one wake at the projected finish:
jobs end early or late, and a fixed-time wake left one finished result unread for almost 4 h.
Background commands in agent harnesses often time out near 2 h, and each wake after a prompt-cache expiry
re-reads the whole context at full price.

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
