---
name: cluster-ops
description: Operate on a shared batch cluster (LSF primary, SLURM equivalents). Use when the user says "run this on the cluster", "submit a job", "bsub", "sbatch", "why is my job pending", "check my jobs", "kill that job", or when you are about to ssh to a login node, copy data to or from a cluster, write a job script, or wait on a running job.
---

# Cluster operations

Goal: work gets done on the cluster with the fewest wasted allocations, and every claim about a job is backed
by evidence from the scheduler, the job's own status file, and its outputs.

## First, know the site

Read `sites/<site>.md` from this repo if the user's cluster has one, and the user's `CLAUDE.md`/`AGENTS.md`
for their group and scratch path. Otherwise discover once and ask the user to confirm:
`lsid` or `sinfo -s`, `bqueues` or `sinfo`, `lfs df -h` or `df -h`, `findmnt -T "$PWD"`.
Never hard-code queue limits from memory. Check them (`bqueues -l <q>`, `scontrol show partition <p>`).

## Login-node etiquette

The login node is shared by everyone. Run only light commands there: `bjobs`, `bqueues`, `lfs quota`, a
bounded `stat`, editing files, `bsub`. Scans (`du`, `find`, `ls -R`), compression, `pip install`, builds and
anything over a minute of CPU go in a job. See `storage-footprint` for disk questions.

## Interactive or batch: one rule

- **Interactive is for probes**: environment, imports, one batch, a throughput or memory probe. Always under
  `safe-interactive` (`hpc-session` plus a short `-W`). An interactive allocation without both is a bug.
- **Everything that runs longer than you are guaranteed to be watching is a batch job** (or a task in a
  worker queue, see `cluster-autoresearch`). Batch jobs end on their own when the agent stops.

## Submitting

LSF memory is per job and the queue default is often tiny (100 MB on some sites). Always pass it.

```bash
bsub -J name -q <queue> -n 4 -M 8000 -R "select[mem>=8000] rusage[mem=8000] span[hosts=1]" \
     -W 4:00 -o logs/%J.out -e logs/%J.err -wa USR2 -wt 10 bash job.sh
```

- `-n` matches the threads flag of the tool. `span[hosts=1]` keeps threads on one node.
- `-M` and `rusage` come from a measured peak (`right-size-jobs`). With nothing measured, say so and pick a
  modest first-run value.
- `-W` always. Shorter walltime backfills sooner and fits shorter queues.
- Arrays: `-J 'name[1-500]%50'` with `%I` in log names. Housekeeping: add `-sp 1` (low priority).
- `-wa USR2 -wt 10` sends USR2 ten minutes before the limit so the job can save and publish.
- Before any submission run `fair-use-preflight`.
- SLURM equivalents are in `references/slurm.md`. LSF details are in `references/lsf.md`.

## Job scripts

Start from `references/job-script-template.sh`. The parts that matter:

- `source /etc/profile || true` and module/env setup first, then `set -Eeuo pipefail`. Some site profiles
  return non-zero and silently abort a script that set `-e` too early.
- Call an absolute interpreter (`$ENV/bin/python`), not whatever `activate` left on `PATH`.
- Work in a node-local dir under `${TMPDIR:-/tmp/$USER/$JOBID}` and prove it is local with `findmnt -T`.
- An EXIT trap removes the work dir and kills helper pids and the process group. It must not change the exit
  code: capture `rc=$?` first, put `|| true` on every cleanup step, end with `exit $rc`. A cleanup `kill` of
  an already-dead helper returns 1, which turns a finished job into EXIT and blocks `done()` dependents.
- Background helper loops (log or checkpoint copiers, samplers) inherit `set -e` and the ERR trap and die
  silently at their first failing command. Start each in its own subshell with its own error handling
  (`( set +eE; trap - ERR; while sleep 300; do ...; done ) &`), record its pid, and check during the run
  that what it publishes keeps advancing. A dead helper is fixed or restarted while the work continues.
  Never kill the main run because a helper died unless you have confirmed its progress is saved.
- Fit the walltime to the measured rate. After the first steps the job computes its ETA and, if it cannot
  finish before the limit, saves and exits with a clear message instead of being killed at 8 % done. Before
  relaxing a request so the job can land on slower hardware, time one unit (an epoch, a shard) there and
  check that the whole run fits in `-W`.
- CPU-only stages (scoring with CPU models, packing, merging) go in CPU jobs, not inside a GPU allocation.
- Replace a script that a running job uses by writing a new file and `mv`. Bash reads scripts as it goes, so
  editing in place corrupts running jobs. Call scripts as `bash script.sh` (copies lose the exec bit).
- Guard only optional diagnostics with `|| true`. Never let `|| true`, `tee` or a trailing `echo` hide the
  exit code of the real work.

## Monitoring without blocking

Bad: one tool call running `bjobs` in a `while sleep` loop for two hours.
Good: the job writes `status.json` (phase, progress, last metric, timestamp). You poll every 30 to 60 s with a
background watcher that exits on a terminal state, then read `status.json`, the log tail and the outputs.
For waits longer than about an hour, don't keep a wait open or `sleep` in a loop: write the job ids and the
next step to a state file and schedule one wake at the projected finish (or let a model-free watcher wake you
on a state change). A pending job past the time you expected is news. Report it with its reason.

`DONE` is a scheduler state, not a verdict. A job is finished when its outputs exist, have the expected count
and checksums, and the log has no silent fallback. Details in `references/monitoring.md`. Long jobs must
checkpoint and survive a kill (`references/checkpointing.md`).

## Transfers

Downloads can arrive truncated without an error. Compare `sha256sum` on both sides before using a file.
Check an exact path with `stat` before concluding a file is missing (a truncated listing lies).

## Killing and replacing

- `bkill`/`scancel` the exact confirmed job id, never a pattern, and only with the user's reason.
- Wait for `EXIT` before reusing its output paths, or write to a new versioned directory.
- Never submit speculative duplicates "in case one pends".
- Check the reply of every kill. With an expired login it can fail silently and the job keeps running.
- A conditional approval ("stop it if progress is saved") is checked before killing. If it fails, ask.
- No kill lines inside reusable submit scripts, no kills by name pattern, no `bkill -r` (it leaves zombie
  jobs). Say so when a checkpoint promised for a later resume is abandoned.
- Dependencies: `done()`/`afterok` only fire on exit 0 (see Job scripts). When an upstream may end non-zero
  with usable output, use `ended()`/`afterany` and have the dependent check the status file first. Re-check
  dependents after any kill (`references/lsf.md`).

## Why is it pending?

`bjobs -p -l <id>` (SLURM: `squeue -j <id> -o %R`, `squeue --start`). Common causes:

- Memory request larger than what free hosts have: right-size it.
- "Affinity ... not enough processor units" or "no suitable hosts" can mean CPU binding, but some LSF sites
  print it whenever no host passes the whole request, including a GPU-memory (`gmem`) filter that only the
  largest GPUs meet, or a full queue. Diagnose before changing anything: read every reason and host in
  `bjobs -p -l`, check free GPU memory per host (`bhosts -gpu -l`, `lshosts -gpu`) and the queue's hosts
  (`bqueues -l <q>`), then change one thing at a time, GPU memory first, then cores, then memory. Never
  shrink cores or data workers below the measured best just to start sooner: one team lost about 4 days
  and 140 GPU-hours training at 2 to 23 % GPU use that way.
- A hard-coded host selector or a host excluded by a stale bad-host list.
- Fairshare: your group used a lot recently. Smaller, shorter jobs backfill.

## Access

If the cluster uses short-lived certificates (Teleport, OIDC, Kerberos), renew before long waits. Expiry
blinds monitoring but does not kill jobs. Re-authenticate before deciding anything about job state.

## Done means

- Job state from the scheduler, `status.json`, the log tail and the output check all agree.
- Resource use was recorded (`right-size-jobs`) so the next request is measured.
- Interactive allocations are released, as shown by the scheduler, and helper processes are gone.
- You list what you could not verify.
