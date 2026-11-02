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

For multi-hour jobs, a wake-up every 20 to 30 minutes that reads `status.json` is enough.

## When it ends

A terminal scheduler state is news, not a verdict. Check, in order:

1. Exit code and reason (`bjobs -l`, `bacct -l`, `sacct`).
2. The last 50 lines of the log and the whole `.err` file. Search for silent fallbacks: warnings, "falling
   back", "retrying on CPU", "skipped".
3. Output invariants: file exists, expected row or file count, checksum matches the receipt, the parser
   reads it.
4. Resource use for the ledger (`right-size-jobs`).

Report what you verified and what you could not.
