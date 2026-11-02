# Checkpointing and recovery

A job that saves only at the end loses everything to a walltime kill, a memory kill or a node failure.

## Rules

- **Save often, publish rarely.** Write checkpoints every N minutes or epochs to node-local disk. Publish only
  `last` and `best` to shared storage, each as one file replaced by atomic `mv`, with a small receipt
  (step, metric, SHA-256).
- **Trap the warning.** Submit with `-wa USR2 -wt 10` (SLURM `--signal=B:USR2@600`). The trap publishes
  `last`, the log and a final status, then exits.
- **Write atomically.** `x.tmp` then `mv x.tmp x`. A restart treats any `.tmp` as absent. File existence is
  not completion: check the receipt hash and expected shape.
- **Resume into a new attempt folder** (`attempt2/`), reading `last` from the previous one. Never overwrite
  the evidence of a failed attempt.
- **Resume at the right granularity.** For many independent units (samples, chromosomes, parameter values),
  one output per unit plus a manifest of finished units means a restart only redoes what is missing.

## Minimal bash pattern

```bash
publish() {   # copy a node-local file to shared storage atomically, with a receipt
  local src=$1 dst=$2
  cp -f "$src" "$dst.tmp" && mv -f "$dst.tmp" "$dst"
  sha256sum "$dst" > "$dst.sha256.tmp" && mv -f "$dst.sha256.tmp" "$dst.sha256"
}
trap 'publish "$WORK/last.ckpt" "$OUT/last.ckpt"; exit 140' USR2
```

## Background helpers

A loop that copies logs or checkpoints in the background must have its pid recorded and killed in the EXIT
trap. A forgotten helper keeps the job alive after the work ends and can overwrite the final status.

A helper started under `set -e` with an ERR trap dies at its first failing command, without a message. Run
it in a subshell with errexit and nounset off (`( set +eEu; trap - ERR; ... ) &`, see the job template), and
alert when the checkpoint it publishes is older than two intervals. If it died, restart it while the run
continues. Never kill the run because a helper died unless you have confirmed its progress is saved: that
throws away every step since the last checkpoint that actually reached shared storage.

## Walltime fitted to the measured rate

Measure the rate after warm-up (the second epoch, or steps 100 to 300) and write the ETA to `status.json`.
If the ETA is above 1.2x the time left before the limit, publish `last` first, then exit with a clear
message ("rate too slow on this hardware"), instead of being killed at 8 % done. Before relaxing a request so
the job can land on slower hardware, time one unit (an epoch, a shard) there and check the whole run fits.

## Child processes

Start the main program in its own session (`setsid`) and kill the whole session at the end
(`pkill -s <sid>`). Worker processes of a killed parent are re-parented to init, keep GPU memory, and are
missed by `pgrep -P`.
