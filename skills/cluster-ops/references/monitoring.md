# Monitoring jobs without holding a tool call open

## What the job writes

- `status.json` in the run folder, overwritten in place every few minutes and at each phase change:
  `phase`, `progress` (e.g. epoch 12/60 or 340/500 samples), `elapsed`, `eta`, last and best metric,
  `time`. Answer questions about progress or ETA from this file, not from an estimate.
- The log, copied from node-local disk to the run folder every few minutes (overwritten, not a new file each
  time).
- On exit: a final status (`done` or `failed` with the exit code) and checksums of the outputs.

## How the agent waits

One background watcher polls every 60 s and exits on a terminal state, a long PEND or an error. A foreground
`while sleep` loop blocks the session and never ends on its own.

```bash
# LSF. UNTIL=RUN before a long handoff. Over ssh, put the ssh command inside the timeout.
# Background: set the tool's own timeout parameter >= CAP+180 s (e.g. 3000 s), not only a shell timeout.
# Foreground (600 s tool timeout): CAP=420 PEND_S=300.
J=1234; UNTIL=END; CAP=2700; PEND_S=1200
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
```

What the output means:

- `DONE`, `EXIT` or `GONE` (a confirmed "is not found": finished and purged): read `status.json`, the log and
  `bacct` (When it ends).
- `PEND_LONG`: report the pending reason from `bjobs -p -l` (`lsf.md`, "Why pending"). Fix an over-strict
  request. Otherwise hand off, with the pending reason, to whoever must act. Never leave a pending job without a
  handoff, and never re-arm it as a new background loop.
- Jobs submitted through `scripts/pendlog submit`: run `pendlog started --out DIR` every 10 to 15 min, or from
  the wait loop (it re-checks a job at most every 10 min), so their start, PEND and pending reason are logged.
- `ZOMBI`: report once and read `bacct`. Don't re-run the loop on it.
- `CAP_REACHED`: the loop hit its own limit before the job changed state. Hand off to a watcher (state file
  and job file) instead of starting another background loop. A foreground loop may continue in the same turn with
  the `T0=` value it printed.
- `ERROR` (timeout, authentication or transport failure, empty reply) is not a job state. Check authentication
  first (an expired certificate does not kill jobs), then run the loop again once.
- A `status.json` or checkpoint that stopped advancing while the job is RUN means a helper died or the job
  hangs.

**Short waits (under about 45 min): wait inside one tool call** with the loop above, through your harness's
background facility or as one foreground command within the tool timeout. Never a series of `sleep` tool calls,
repeated short polls or re-armed loops: where a coordinator relays every turn end to a parent chat, each re-arm
wakes the parent. The loop can run for its cap plus one sleep step plus one query timeout. Keep that sum under
the tool's own timeout, or the loop is killed before it reports.

**Long waits (over about 45 min):** first run the loop with `UNTIL=RUN`, and hand off only a running job. Report
a job still pending past `PEND_S` with its reason instead. Once it runs, write the job ids, expected outputs and
the next step to a state file, and let a model-free watcher wake the agent on a state change. Prefer that to one
wake at the projected finish: jobs end early or late.

**Stragglers and long pends also need a wake.** Record an expected runtime for each job. A job still RUN at
about 2x its expected time, or 2x another run of the same step, is a straggler: check the host load (`lsload`,
`bhosts`), start one copy that excludes that host, keep whichever finishes first and kill the other by exact id.
Give a pending job a limit too (for example 3 h). At the limit, re-read the pending reason and the hosts that can
take it, then relax one thing or ask.

**A wait lives in one session.** A login expiry, a network outage, a provider error or a move of the agent to
another account, thread or machine can end it. Whoever made the change re-checks every watched job at once (a
purged job may need `bhist -n 10 -l <id>` on LSF) and re-arms a wait for each.

## When it ends

A terminal scheduler state is news, not a verdict. Check, in order:

1. Exit code and reason (`bjobs -l`, `bacct -l`).
2. The last 50 lines of the log and the whole `.err` file. Search for silent fallbacks: warnings, "falling
   back", "retrying on CPU", "skipped".
3. Output invariants: file exists, expected row or file count, checksum matches the receipt, the parser
   reads it.
4. A worker or wrapper job's DONE says nothing about its tasks. Read the per-task results before reporting.
5. Resource use for the ledger (`scripts/jobeff`, `sizing.md`). If the stage used more than about 1.5x its
   planned GPU-h or core-h, say so and why.

Report what you verified and what you could not.
