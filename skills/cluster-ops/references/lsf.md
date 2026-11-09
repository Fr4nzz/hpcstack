# LSF quick reference

Values differ between clusters and change over time, so check every limit on your own site.

## Discover

```bash
lsid                                   # cluster name and LSF version
bqueues                                # queues, state, pending and running counts
bqueues -l <queue>                     # RUNLIMIT, MEMLIMIT default, HOSTS, job exceptions (IDLE)
bhosts -gpu                            # GPU hosts and their state
lshosts                                # cores and memory per host type
grep -E 'LSF_UNIT_FOR_LIMITS|LSB_MOD_ALL_JOBS' "$LSF_ENVDIR/lsf.conf"   # MB/GB units, bmod on running jobs
```

`LSF_UNIT_FOR_LIMITS=MB` means `-M 4000` is 4 GB. Even with `LSB_MOD_ALL_JOBS=Y`, a site can refuse `bmod -W` on
a running job ("Cannot change job options for a running job"). Test once on a throwaway `sleep` job. If it is
refused, choose `-W` up front and resubmit when it runs out.

## Submit

| Need | Option |
|---|---|
| cores on one host | `-n 8 -R "span[hosts=1]"` |
| memory | `-M 8000 -R "select[mem>=8000] rusage[mem=8000]"` |
| walltime | `-W 4:00` (hours:minutes) |
| GPU | `-gpu "num=1:gmem=<size>G"`. On a shared GPU, a job without a `gmem` floor can fail at model load because another job holds the memory |
| logs | `-o logs/%J.out -e logs/%J.err` (arrays: `%J.%I`) |
| array with cap | `-J 'name[1-500]%50'`, index in `$LSB_JOBINDEX` |
| dependency | `-w "done(<jobid>)"` on exact ids (Dependencies) |
| warning before kill | `-wa USR2 -wt 10` |
| low priority | `-sp 1` |
| pass parameters | `-env "all,PARAM=value"` or script arguments, never by editing the script |

## Inspect

```bash
bjobs -w                                         # your jobs
bjobs -l <id>                                    # RESOURCE USAGE, pending reasons
bjobs -p -l <id>                                 # why pending
bhist -l <id>                                    # timeline including pend time
bacct -l <id>                                    # finished jobs (slow over wide time ranges)
bhist -n 10 -l <id>                              # purged jobs: searches older event files (timeout 120)
bpeek <id>                                       # stdout so far
```

Finished jobs drop out of `bjobs -a` after the site's clean period, and can then be missing from plain `bacct`
and `bhist -l` too. Read the job's own status file first, then `bhist -n 10 -l <id>` (timeout 120 s or more).
`scripts/jobeff --lsf-log` reads the "Resource usage summary" at the end of the `.out` file.

## Modify and kill

```bash
bmod -W 6:00 <id>        # walltime of a pending job (see Discover for running jobs)
bstop / bresume <id>
bkill <id>               # exact id only. bkill -s USR2 <id> sends a signal instead
```

`bmod -M` or `-R` on a running job is often refused, and a site esub can refuse `bmod -R` on pending GPU jobs
too. Resubmit instead. A successful `bkill` replies that the job "is being terminated".

## Dependencies

- `done(<id>)` fires only on exit 0. Use it for an upstream whose exit 0 means verified work, with an EXIT trap
  that keeps that code.
- An upstream that may end non-zero with usable output (an array with killed and retried elements, a job whose
  cleanup you don't control) gets `ended(<id>)`. The dependent first checks every required output (status file,
  receipts, counts) and exits non-zero if any is missing.
- Dependents of a failed upstream pend with "Dependency condition invalid or never satisfied". Kill them by
  exact id (SKILL.md, Recover), then resubmit the chain once the cause is fixed.
- A dependency that fails with "No matching job found" refers to purged job records, not to killed elements. Use
  the real original or retry ids, and re-check dependents with `bjobs -l` after any kill.

## Why pending: "Affinity ... not enough processor units"

Some sites print this, or "no suitable hosts", whenever no host passes the whole request. The block can be CPU
binding, a GPU-memory (`gmem`) filter that only the largest GPUs meet, the memory request, or a full queue.

1. Read every reason and host in `bjobs -p -l <id>`, not just the first line.
2. Check free GPU memory per host (`bhosts -gpu -l`, `lshosts -gpu`) and the hosts the queue reaches
   (`bqueues -l <queue>`).
3. Change one thing at a time: GPU memory first, then cores, then memory. Relax only what is over-strict. Lower
   `gmem` to about 1.3 x the measured peak (SKILL.md, GPU memory), or replace it with a GPU-model constraint
   (`gmodel=`, checked with `lshosts -gpu`) whose memory covers that peak. Never drop the GPU-memory floor: the
   job can then land on a GPU too small for it and fail with CUDA out of memory.

## Odd states

| State or reply | Meaning |
|---|---|
| `UNKWN` | the scheduler lost contact with the node, not EXIT. Query again in a few minutes |
| "Job not found" | finished and purged from `bjobs` (Inspect) |
| `ZOMBI` | a kill the scheduler could not confirm. Ignore it in watchers, report it once |
| PEND with "Affinity ... not enough processor units" | no host fits the whole request (Why pending) |

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

Some queues define an `IDLE` exception (cputime/runtime below a threshold). It is cumulative, only notifies, and
is missing on many GPU and interactive queues, so it does not replace `scripts/hpc-session`.
