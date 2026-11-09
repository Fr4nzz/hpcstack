# Checkpointing and recovery

A job that saves only at the end loses everything to a walltime kill, a memory kill or a node failure.

- **Save often, publish rarely.** Write checkpoints every N minutes or epochs to node-local disk. Publish only
  `last` and `best` to shared storage, each replaced by atomic `mv`, with a small receipt (step, metric, SHA-256).
- **Trap the warning.** Submit with `-wa USR2 -wt 10` (SLURM `--signal=B:USR2@600`). The trap publishes `last`,
  the log and a final status, then exits.
- **Write atomically**: `x.tmp`, then `mv x.tmp x`. A restart treats any `.tmp` as absent. File existence is not
  completion: check the receipt hash and expected shape.
- **Resume into a new attempt folder** (`attempt2/`) from the previous `last`. Keep the evidence of a failed attempt.
- **Many independent units** (samples, shards, parameter values): one output per unit plus a manifest of finished
  units, so a restart redoes only what is missing.

```bash
publish() {   # copy a node-local file to shared storage atomically, with a receipt
  local src=$1 dst=$2
  cp -f "$src" "$dst.tmp" && mv -f "$dst.tmp" "$dst"
  sha256sum "$dst" > "$dst.sha256.tmp" && mv -f "$dst.sha256.tmp" "$dst.sha256"
}
trap 'publish "$WORK/last.ckpt" "$OUT/last.ckpt"; exit 140' USR2
```

## Background helpers

Record the pid of any background loop that copies logs or checkpoints, and kill it in the EXIT trap. Otherwise
it keeps the job alive after the work ends and can overwrite the final status. Under `set -e` with an ERR trap, a
helper dies at its first failing command without a message, so run it in a subshell with errexit and nounset off
(`( set +eEu; trap - ERR; ... ) &`, as in the job template). Alert when the checkpoint it publishes is older than
two intervals, and restart it while the run continues. Never kill the run because a helper died unless its
progress is confirmed saved.

## Walltime fitted to the measured rate

Measure the rate after warm-up (the second epoch, or steps 100 to 300) and write the ETA to `status.json`. If the
ETA is above 1.2 x the time left, publish `last` and exit with a clear message ("rate too slow on this hardware")
rather than be killed at 8 % done. Before relaxing a request so the job can land on slower hardware, time one
unit there and check the whole run fits.

## Child processes

Start the main program in its own session (`setsid`) and kill the whole session at the end (`pkill -s <sid>`).
Workers of a killed parent are re-parented to init, keep GPU memory, and are missed by `pgrep -P`.
