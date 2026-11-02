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
| GPU | `-gpu "num=1"`. Add `mode=shared` and `gmem=<n>G` only when you have a reason |
| logs | `-o logs/%J.out -e logs/%J.err` (arrays: `%J.%I`) |
| array with cap | `-J 'name[1-500]%50'`, index in `$LSB_JOBINDEX` |
| dependency | `-w "done(<jobid>)"`. Prefer `done()` on ids over `ended()` on names |
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

## Exit codes worth knowing

| Code or reason | Meaning |
|---|---|
| `TERM_MEMLIMIT`, exit 130 or 137 | hit `-M`. Measure peak, set `-M` to 1.3x, or cap the tool (Java `-Xmx`) |
| `TERM_RUNLIMIT`, exit 140 | hit `-W` |
| `TERM_OWNER` | you killed it |
| `TERM_CPULIMIT` | CPU time limit |
| exit 1 with "Successfully completed" never printed | the script failed. Read `.err` |

## Job exceptions

Some queues define `IDLE` (cputime/runtime below a threshold) and notify. It is cumulative, it only notifies,
and many GPU and interactive queues don't define it. It does not replace `safe-interactive`.
