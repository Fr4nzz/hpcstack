# LSF quick reference

Check every limit on your own site. Values differ between clusters and change over time.

## Discover

```bash
lsid                                   # cluster name and LSF version
bqueues                                # queues, state, pending and running counts
bqueues -l <queue>                     # RUNLIMIT, MEMLIMIT default, HOSTS, job exceptions (IDLE)
bhosts -gpu                            # GPU hosts and their state
lshosts                                # cores and memory per host type
grep -E 'LSF_UNIT_FOR_LIMITS|LSB_MOD_ALL_JOBS' "$LSF_ENVDIR/lsf.conf"   # MB/GB units, bmod on running jobs
```

`LSF_UNIT_FOR_LIMITS=MB` means `-M 4000` is 4 GB. `LSB_MOD_ALL_JOBS=Y` alone does not mean users can extend a
running job: on at least one site with it set, `bmod -W` on a running job answers "Cannot change job options
for a running job". Test once on a throwaway `sleep` job. If it fails, choose `-W` up front and resubmit.

## Submit

| Need | Option |
|---|---|
| cores on one host | `-n 8 -R "span[hosts=1]"` |
| memory | `-M 8000 -R "select[mem>=8000] rusage[mem=8000]"` |
| walltime | `-W 4:00` (hours:minutes) |
| GPU | `-gpu "num=1:gmem=<n>G"`. On shared GPUs a job without a `gmem` floor can fail at model load because another job holds the memory |
| logs | `-o logs/%J.out -e logs/%J.err` (arrays: `%J.%I`) |
| array with cap | `-J 'name[1-500]%50'`, index in `$LSB_JOBINDEX` |
| dependency | `-w "done(<jobid>)"` on ids. See "Dependencies" below |
| warning before kill | `-wa USR2 -wt 10` |
| low priority | `-sp 1` |
| pass parameters | `-env "all,PARAM=value"` or script arguments, never by editing the script |

## Inspect

```bash
bjobs -w                                         # your jobs
bjobs -l <id>                                    # RESOURCE USAGE, pending reasons
bjobs -p -l <id>                                 # why pending
bhist -l <id>                                    # timeline including pend time
bacct -l <id>                                    # finished jobs (can be slow, avoid wide time ranges)
bhist -n 10 -l <id>                              # purged jobs: searches older event files (timeout 120)
bpeek <id>                                       # stdout so far
```

Finished jobs drop out of `bjobs -a` after the site's clean period, and can then be missing from plain `bacct`
and `bhist -l` too. Read the job's own status file first, then `bhist -n 10 -l <id>` (timeout 120 s or more).
The `.out` file's "Resource usage summary" is read by `scripts/jobeff --lsf-log`.

## Modify and kill

```bash
bmod -W 6:00 <id>        # walltime of a pending job (running jobs: often refused, test first)
bstop / bresume <id>
bkill <id>               # exact id only. bkill -s USR2 <id> sends a signal instead
```

- `bmod -M` or `-R` on a running job is often refused, and a site esub can refuse `bmod -R` on pending GPU
  jobs too. Resubmit instead. A successful `bkill` replies that the job "is being terminated".

## Dependencies

- `done(<id>)` fires only if the upstream exits 0. Use it only when the upstream script exits 0 after
  verified work and its EXIT trap keeps that code.
- When an upstream may end non-zero with usable output (arrays with killed and retried elements, jobs whose
  cleanup you don't control), use `ended(<id>)` and have the dependent check every required output (status
  file, receipts, counts) first and exit non-zero if any is missing.
- Dependents of a failed upstream pend with "Dependency condition invalid or never satisfied". Resubmit the
  chain once the cause is fixed.
- A dependency fails ("No matching job found") when the referenced job records have been purged, not because
  elements were killed. Use the real original or retry ids, and re-check dependents with `bjobs -l` after any
  kill.

## Why pending: "Affinity ... not enough processor units"

Some sites print this, or "no suitable hosts", whenever no host passes the whole request: CPU binding, a
GPU-memory (`gmem`) filter that only the largest GPUs meet, the memory request, or a full queue.

1. Read every reason and host in `bjobs -p -l <id>`, not just the first line.
2. Check free GPU memory per host (`bhosts -gpu -l`, `lshosts -gpu`) and the hosts the queue reaches
   (`bqueues -l <q>`).
3. Change one thing at a time, GPU memory first, then cores, then memory. Relax only what is over-strict:
   lower `gmem` to the sizing rule's value, or replace it with a GPU-model constraint
   (`gmodel=`, checked with `lshosts -gpu`) whose memory covers that peak. Never drop the GPU-memory floor: the
   job can then land on a GPU too small for it and fail with CUDA out of memory.

## Odd states

| State or reply | Meaning |
|---|---|
| `UNKWN` | the scheduler lost contact with the node, not EXIT. Query again in a few minutes |
| "Job not found" | finished and purged from `bjobs`. Read the job's status file, then `bhist -n 10 -l <id>` |
| `ZOMBI` | a kill the scheduler could not confirm. Ignore it in watchers, report it once |
| PEND with "Affinity ... not enough processor units" | no host fits the whole request. See "Why pending" above |

## Exit codes worth knowing

| Code or reason | Meaning |
|---|---|
| `TERM_MEMLIMIT`, exit 130 or 137 | hit `-M`. Re-size from the measured peak, or cap the tool (Java `-Xmx`) |
| `TERM_RUNLIMIT`, exit 140 | hit `-W` |
| `TERM_OWNER` | you killed it |
| `TERM_CPULIMIT` | CPU time limit |
| exit 1 with "Successfully completed" never printed | the script failed, or its EXIT trap returned non-zero after the work. Read `.err` and the status file |
| exit 141 | `SIGPIPE`: the script stopped at a `cmd \| head` under `pipefail`. Check whether the work after that line ran, then fix the pipe. Not a finished job |

## Job exceptions

Some queues define `IDLE` (cputime/runtime below a threshold) and notify. It is cumulative, it only notifies,
and many GPU and interactive queues don't define it. It does not replace `scripts/hpc-session`.
