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
| GPU | `-gpu "num=1"`. Add `gmem=<n>G` only at about 1.3x a measured GPU-memory peak (it excludes smaller GPUs) |
| logs | `-o logs/%J.out -e logs/%J.err` (arrays: `%J.%I`) |
| array with cap | `-J 'name[1-500]%50'`, index in `$LSB_JOBINDEX` |
| dependency | `-w "done(<jobid>)"` on ids, only if the upstream script exits 0 after verified work and its EXIT trap keeps that code. For arrays whose elements may be killed and retried, use the retries' ids, or `ended(<id>)` plus a status check in the dependent job. Re-check with `bjobs -l` after any kill |
| warning before kill | `-wa USR2 -wt 10` |
| low priority | `-sp 1` |
| pass parameters | `-env "all,PARAM=value"` or script arguments, never by editing the script |

Exclusive GPU mode blocks co-tenants. Use it only when shared mode is measured to be too slow and the user
agrees.

## Inspect

```bash
bjobs -w                                         # your jobs
bjobs -o 'jobid stat queue slots cpu_used run_time max_mem memlimit idle_factor exit_code' -json
bjobs -l <id>                                    # RESOURCE USAGE, pending reasons
bjobs -p -l <id>                                 # why pending
bhist -l <id>                                    # timeline including pend time
bacct -l <id>                                    # finished jobs (can be slow, avoid wide time ranges)
bpeek <id>                                       # stdout so far
```

Finished jobs drop out of `bjobs -a` after the site's clean period. The `.out` file of every job ends with a
"Resource usage summary" (CPU time, max memory, run time). `right-size-jobs/scripts/jobeff --lsf-log` reads it.

## Modify and kill

```bash
bmod -W 6:00 <id>        # walltime of a pending job (running jobs: often refused, test first)
bstop / bresume <id>
bkill <id>               # exact id only. bkill -s USR2 <id> sends a signal instead
```

- `bmod -M` or `-R` on a running job is often refused, and a site esub can refuse `bmod -R` on pending GPU
  jobs too. Resubmit instead.
- Check that the `bkill` reply says the job "is being terminated". Avoid `bkill -r`: it can leave ZOMBI jobs.
- `-w` on an array with killed elements can fail with "No matching job found". Depend on the retries.

## Odd states

| State or reply | Meaning |
|---|---|
| `UNKWN` | the scheduler lost contact with the node, not EXIT. Query again in a few minutes |
| "Job not found" | finished and purged from `bjobs`. Read the job's status file and `bacct` |
| `ZOMBI` | a kill the scheduler could not confirm. Ignore it in watchers, report it once |
| PEND with "Affinity ... not enough processor units" | no host fits the whole request (cores, memory or a `gmem` filter). See `cluster-ops`, "Why is it pending?" |

## Exit codes worth knowing

| Code or reason | Meaning |
|---|---|
| `TERM_MEMLIMIT`, exit 130 or 137 | hit `-M`. Measure peak, set `-M` to 1.3x, or cap the tool (Java `-Xmx`) |
| `TERM_RUNLIMIT`, exit 140 | hit `-W` |
| `TERM_OWNER` | you killed it |
| `TERM_CPULIMIT` | CPU time limit |
| exit 1 with "Successfully completed" never printed | the script failed, or its EXIT trap returned non-zero after the work. Read `.err` and the status file |
| exit 141 | `SIGPIPE`, often `pipefail` plus `cmd \| head`. Not a work failure in itself |

## Job exceptions

Some queues define `IDLE` (cputime/runtime below a threshold) and notify. It is cumulative, it only notifies,
and many GPU and interactive queues don't define it. It does not replace `safe-interactive`.
