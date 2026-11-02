---
name: right-size-jobs
description: Set cluster resource requests (cores, memory, walltime, GPUs) from measured usage. Use when the user asks "how much memory should I ask for?", "how many cores?", "why was my job killed (TERM_MEMLIMIT, OOM, exit 137/140)?", "was that job efficient?", after any batch job or array finishes, or before resubmitting a job with a bigger request.
---

# Right-size jobs

Goal: the next request matches what the job actually used, with a stated margin. Over-requesting is not
safe padding. It makes your job pend longer, burns your group's fairshare, and blocks co-tenants on shared
nodes.

## Measure

Run the helper on finished or running jobs:

```bash
scripts/jobeff 1234 1235          # LSF via bjobs -json (falls back to bacct for old jobs)
scripts/jobeff --lsf-log logs/*.out   # LSF .out files ("Resource usage summary"), works offline
scripts/jobeff --slurm 98765      # SLURM via sacct
scripts/jobeff --user             # your recent jobs (LSF bjobs -a, or SLURM sacct over 2 days)
```

It prints one line per job: cores, CPU efficiency, peak and requested memory, run time, a suggested next
request, and flags. Raw sources if you need them: `references/bacct-fields.md`.

For Nextflow, read `-with-trace` (`%cpu`, `peak_rss`, `realtime`). For GPUs, sample
`nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 10` during a probe.

## Rules

- **CPU efficiency** = CPU time / (wall time x cores). Below 0.5, the job asked for too many cores, or the
  tool's thread flag does not match `-n`. Fix the flag first, then set `-n` to the cores it really uses.
  A "2-thread" tool that uses 1.05 cores gets 1 core.
- **Memory**: `-M` is about 1.3 x measured peak, rounded up to the next 500 MB. Peak is what the scheduler
  counts (cgroup RSS), not page cache.
- **Variable inputs**: fit peak against input size and scale the request per input, or retry with more
  memory on a memory kill. Don't pad every job for the largest input.
- **Walltime**: about 1.5 x the observed p95 run time. Shorter walltimes backfill sooner.
- **GPU**: host RAM and VRAM are separate budgets. A CUDA OOM is not `TERM_MEMLIMIT`. A GPU under about
  80 % busy means the input pipeline is the bottleneck (`optimize-pipeline`), not that you need a second GPU.
- **Java**: if memory grows until the kill, the heap is uncapped. See `genomics-jobs` (`-Xmx`).
- **Raising `-M` after every kill is not a fix.** Find what grows first.

Bad: "It died at 30 GB, resubmit with 80 GB."
Good: "Peak was 7.4 GB per JVM with no `-Xmx`. The heap grew to the limit. Set `-Xmx6g`, `-M 8000`, and
check the next peak."

## Flags from `jobeff`

| Flag | Meaning | Action |
|---|---|---|
| `LOW_CPU` | efficiency < 0.5 | lower `-n` or fix the threads flag |
| `MEM_OVER` | peak / requested < 0.4 | lower `-M` to the suggestion |
| `MEM_NEAR` | peak / requested > 0.95 | raise `-M` a little, or the next input may die |
| `IDLE` | LSF idle factor < 0.25 | the job waited on something. Find out what |
| `FAILED` | non-zero exit | read the log before sizing from it |

## Ledger

Append each verified run to a ledger (template in `examples/ledger.md` at the repo root): date, task, input
size, resources, run time, peak memory, throughput. Read it before sizing a new job, so nobody repeats a
smoke test that was already done.

## Done means

- The next submission's `-n`, `-M` and `-W` each cite a measured number and a margin.
- Flags from `jobeff` are either fixed or explained.
- The ledger has a new row.
