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
    UNKWN|PSUSP|USUSP|SSUSP) ;;   # UNKWN = lost node contact, not EXIT: keep polling
    *) if [[ $r == *"is not found"* ]]; then echo GONE; else echo "ERROR rc=$rc: $r"; fi; break ;;
  esac
  sleep 60
done
# SLURM
until s=$(sacct -n -X -P -j 1234 -o State 2>/dev/null | head -1); [[ $s =~ ^(COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY) ]]; do sleep 60; done; echo "$s"
```

What the output means:

- `DONE`, `EXIT`, or `GONE` (a confirmed "is not found": finished and purged) are news, not verdicts. Read
  `status.json`, the log and `bacct` (see "When it ends").
- `PEND_30MIN`: report the pending reason from `bjobs -p -l` (`lsf.md`, "Why pending"). Don't re-arm silently.
- `ERROR` (timeout, authentication or transport failure, or an empty reply) is not a job state. The job stays
  tracked. Check authentication first (an expired certificate does not kill jobs), then run the loop again.
- A `status.json` or checkpoint that stopped advancing while the job is RUN means a helper died or the job
  hangs.

**Short waits (under about 1 h): wait inside one tool call** with the loop above, using your harness's
background facility (a background command or monitor), or one foreground command within the tool timeout.
Never a series of `sleep` tool calls or repeated short polls.

**Long waits (over about 1 h):** first run the loop with `UNTIL=RUN`, and hand off only a running job. A
watcher that wakes on ended jobs does not wake on PEND, so a job still pending after 30 min is reported with
its reason instead. Once it runs, write the job ids, expected outputs and the next step to a state file, then
let a model-free watcher wake the agent on a state change, or schedule one wake at the projected finish.
Background commands in agent harnesses often time out near 2 h, and each wake after a prompt-cache expiry
re-reads the whole context at full price.

## When it ends

A terminal scheduler state is news, not a verdict. Check, in order:

1. Exit code and reason (`bjobs -l`, `bacct -l`, `sacct`).
2. The last 50 lines of the log and the whole `.err` file. Search for silent fallbacks: warnings, "falling
   back", "retrying on CPU", "skipped".
3. Output invariants: file exists, expected row or file count, checksum matches the receipt, the parser
   reads it.
4. Resource use for the ledger (`right-size-jobs`).

Report what you verified and what you could not.
