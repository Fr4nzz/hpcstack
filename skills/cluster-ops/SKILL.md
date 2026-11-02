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
- An EXIT trap removes the work dir and kills helper pids and the process group.
- Replace a script that a running job uses by writing a new file and `mv`. Bash reads scripts as it goes, so
  editing in place corrupts running jobs. Call scripts as `bash script.sh` (copies lose the exec bit).
- Guard only optional diagnostics with `|| true`. Never let `|| true`, `tee` or a trailing `echo` hide the
  exit code of the real work.

## Monitoring without blocking

Bad: one tool call running `bjobs` in a `while sleep` loop for two hours.
Good: the job writes `status.json` (phase, progress, last metric, timestamp). You poll every 30 to 60 s with a
background watcher that exits on a terminal state, then read `status.json`, the log tail and the outputs.

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

## Why is it pending?

`bjobs -p -l <id>` (SLURM: `squeue -j <id> -o %R`, `squeue --start`). Common causes:

- Memory request larger than what free hosts have: right-size it.
- "Affinity ... not enough processor units" is CPU binding, not GPUs.
- A hard-coded host selector or a host excluded by a stale bad-host list.
- Fairshare: your group used a lot recently. Smaller, shorter jobs backfill.

## Access

If the cluster uses short-lived certificates (Teleport, OIDC, Kerberos), renew before long waits. Expiry
blinds monitoring but does not kill jobs. Re-authenticate before deciding anything about job state.

## Done means

- Job state from the scheduler, `status.json`, the log tail and the output check all agree.
- Resource use was recorded (`right-size-jobs`) so the next request is measured.
- Interactive allocations are released and helper processes are gone.
- You list what you could not verify.
