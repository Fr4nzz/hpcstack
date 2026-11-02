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

Good: one background watcher that polls every 30 to 60 s and exits on a terminal state.

```bash
# LSF
until s=$(bjobs -noheader -o stat 1234 2>/dev/null); [[ $s == DONE || $s == EXIT || -z $s ]]; do sleep 60; done; echo "$s"
# SLURM
until s=$(sacct -n -X -P -j 1234 -o State 2>/dev/null | head -1); [[ $s =~ ^(COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY) ]]; do sleep 60; done; echo "$s"
```

Run it with your harness's background facility (Claude Code: a background Bash call or Monitor. Codex: a
background terminal). If the watcher runs over ssh, wrap each poll in `timeout 60` so a dead connection
cannot hang it. If polls fail, check authentication first. An expired certificate does not kill jobs.

For waits longer than about an hour, end the session's wait instead of holding it open. Background commands
in agent harnesses often time out near 2 h, and each wake after a prompt-cache expiry re-reads the whole
context at full price. Write the job ids, expected outputs and the next step to a state file, then schedule
one wake at the projected finish, or let a model-free watcher wake the agent only on a state change. Never a
series of `sleep` calls.

A watcher reports more than DONE and EXIT:

- a PEND longer than you expected (say 30 min), with its pending reason. Don't re-arm silently.
- an empty answer ("Job not found"): the job is finished and purged, so read `status.json`.
- `UNKWN`: lost node contact, not a failure. Query again in a few minutes.
- a `status.json` or checkpoint that stopped advancing while the job is RUN: a helper died or the job hangs.

## When it ends

A terminal scheduler state is news, not a verdict. Check, in order:

1. Exit code and reason (`bjobs -l`, `bacct -l`, `sacct`).
2. The last 50 lines of the log and the whole `.err` file. Search for silent fallbacks: warnings, "falling
   back", "retrying on CPU", "skipped".
3. Output invariants: file exists, expected row or file count, checksum matches the receipt, the parser
   reads it.
4. Resource use for the ledger (`right-size-jobs`).

Report what you verified and what you could not.
