# Sizing: where the numbers come from

## `jobeff` flags

| Flag | Meaning | Action |
|---|---|---|
| `LOW_CPU` | efficiency < 0.5 | lower `-n` or fix the threads flag |
| `MEM_OVER` | peak / requested < 0.4 | lower `-M` to the suggestion |
| `MEM_NEAR` | peak / requested > 0.95 | raise `-M` a little, or the next input may die |
| `IDLE` | LSF idle factor < 0.25 | the job waited on something. Find out what |
| `FAILED` | non-zero exit | read the log before sizing from it |

To audit a project, run `jobeff --lsf-log` over every existing log first. Sum core-hours allocated, used, and
spent in failed or killed jobs, then fix the steps that lose the most.

## Raw sources

- LSF running or recent: `bjobs -a -o 'jobid stat slots cpu_used run_time max_mem memlimit idle_factor exit_code'
  -json <id>`. An empty `MEMLIMIT` means the queue default applied. `bjobs -a` keeps finished jobs only for the
  site's clean period, often an hour. After that, `bacct -l <id>` (run time = TURNAROUND - WAIT), given job ids,
  not a wide time range.
- Every LSF `.out` file ends with a "Resource usage summary", and cores come from "executed on host(s)
  <8*node>" (no `N*` means 1). It is often the most reliable source months later.
- SLURM: `sacct -j <id> -P -o JobID,State,Elapsed,TotalCPU,AllocCPUS,ReqMem,MaxRSS` or `seff <id>`. `MaxRSS` is
  per step, so take the maximum over steps.

## Nextflow trace

Run with `-with-trace` (fields `name, %cpu, peak_rss, realtime, cpus, memory, exit, attempt`). Group by process
and use the p95, not one outlier:

- `%cpu / (100 x cpus)` below 0.5: lower `cpus` for that process.
- `peak_rss / memory` below 0.4: lower `memory`, and retry outliers with more. The exit codes below include
  memory kills (130, 137) and the walltime (140), so scale both memory and time per attempt, capped by
  `resourceLimits`:

  ```groovy
  process {
    errorStrategy  = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
    maxRetries     = 2
    memory         = { 4.GB * task.attempt }
    time           = { 4.h  * task.attempt }
    resourceLimits = [cpus: 16, memory: 64.GB, time: 2.d]   // the site's real limits
  }
  ```
- Check the executor's memory semantics. Without `perJobMemLimit = true` on a site where `-M` is per job, Nextflow
  divides memory by cores and every request is wrong by a factor of the core count.
- Keep the executor throttles the site profile sets (`queueSize`, `submitRateLimit`). `process.scratch = true`
  runs tasks on node-local disk, and one shared container cache per group saves a copy per user.

## Wait risk

PEND time cannot be predicted from the request: cluster-wide load drives the long waits. A study of 10,490 LSF
jobs found no model that caught any GPU-queue wait over 1 h. The request still changes the risk:

- GPU memory above the smallest large-GPU class (gmem over 32G there) added 28 points to the chance of waiting
  over 1 h. Fit under it when the measured peak allows (smaller batch, half precision), never below 1.3 x the
  measured GPU-memory peak.
- Cutting cores had no average effect. It helps only when the pending reason says the fitting hosts lack cores.
- Model or host pins shrink the eligible hosts. Drop a pin the job does not need.

Submit through `scripts/pendlog submit --out DIR -- bsub ...` and run `scripts/pendlog started --out DIR` every
10 to 15 min while jobs pend. Before a batch, `scripts/pend-risk --history jobs.csv --request "<bsub args>"` on
`pendlog export` output or an accounting CSV prints how often similar recent jobs waited over 1 h, never an ETA.

## Ledger

Keep one in the project. One row per verified run (exit 0, outputs checked), with the peak the scheduler
measured. Note when a job hit its limit (the true peak is then unknown). A new tool version or input type gets a
new row, not an edit.

| Date | Task | Input size | Queue | Cores | -M (MB) | GPU | Wall | CPU eff | Peak mem | Throughput | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | jobeff, log |

## Preflight receipt

Write it to the run folder as `PREFLIGHT.md` and show it to the user. Mark items that do not apply n/a.

```text
Preflight <date> <job name>
Request: -q normal -n 4 -M 6000 -W 3:00, array [1-120]%30. Source: ledger row <date> (peak 4.4 GB, 0.93 eff).
Cost estimate: 120 x 2.1 h x 4 cores = about 1,000 core-h. GPUs: none.
Storage: group files 72 % used. New files: 240 (2 per task). Node-local temp: yes (findmnt: xfs). Output: out/v3/.
Recovery: status.json every 5 min, USR2 trap, skip-if-done per unit. Deps: none.
Done check: 120 outputs, checksums, per-task receipts.
Open items: none.
```
