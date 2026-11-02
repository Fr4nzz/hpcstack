# Where the numbers come from

## LSF, running or recently finished (`bjobs`)

```bash
bjobs -a -o 'jobid stat queue slots cpu_used run_time max_mem avg_mem memlimit idle_factor exit_code' -json 1234
```

| Field | Meaning | Note |
|---|---|---|
| `SLOTS` | cores allocated | |
| `CPU_USED` | CPU time so far, e.g. `5321.0 second(s)` | |
| `RUN_TIME` | wall time, e.g. `3600 second(s)` | |
| `MAX_MEM`, `AVG_MEM` | peak and average memory, e.g. `1.2 Gbytes` | cgroup RSS on most sites |
| `MEMLIMIT` | the `-M` limit, e.g. `8 G` | empty if none was set (the queue default applies) |
| `IDLE_FACTOR` | cputime / runtime | below about 0.25 the job mostly waited |
| `EXIT_CODE` | exit status | |

`bjobs -a` keeps finished jobs only for the site's clean period (often an hour).

## LSF, finished (`bacct -l`)

```
Accounting information about this job:
     CPU_T     WAIT     TURNAROUND   STATUS     HOG_FACTOR    MEM    SWAP
   5321.00       40         3640       done         1.4618   7.4G     0M
```

Run time = TURNAROUND - WAIT. Cores come from the "Dispatched N Task(s)" line (older versions:
"Submitted ... <N> Processors Requested"). `bacct` scans accounting files, so give it job ids, not a wide
time range, and expect it to be slow.

## LSF, the job's `.out` file

Every LSF output file ends with:

```
Resource usage summary:

    CPU time :                                   5321.00 sec.
    Max Memory :                                 7578 MB
    Total Requested Memory :                     8000.00 MB
    Max Processes :                              4
    Max Threads :                                61
    Run time :                                   3600 sec.
```

Cores come from the "executed on host(s) <8*node>" line near the top (no `N*` prefix means 1). This is often
the most reliable source months later. `jobeff --lsf-log` parses it.

## SLURM

```bash
sacct -j 98765 -P -o JobID,State,Elapsed,TotalCPU,AllocCPUS,ReqMem,MaxRSS,Timelimit
seff 98765
```

- `MaxRSS` is per step. Take the maximum across the `.batch`, `.extern` and numbered steps.
- `ReqMem` may end in `n` (per node) or `c` (per core) on older versions. Per core means multiply by cores.
- `TotalCPU` on the allocation line sums its steps.

## Nextflow

`-with-trace` columns `%cpu` (100 = one core), `peak_rss`, `realtime`, `cpus`, `memory`. Group by process
name and size each label from the p95.
