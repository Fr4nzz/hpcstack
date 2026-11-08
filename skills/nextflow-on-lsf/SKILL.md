---
name: nextflow-on-lsf
description: Run Nextflow pipelines (nf-core, sanger-tol, custom) on an LSF or SLURM cluster. Use when the user says "run nf-core on the farm", "run this Nextflow pipeline", "nextflow run", "which Nextflow profile?", "my Nextflow work dir is huge", "Nextflow tasks keep getting killed for memory", or asks how to set process resources, retries or executor limits.
---

# Nextflow on a batch cluster

Goal: the pipeline runs from a small head job, tasks are sized from the trace, failures retry with more
resources instead of padding everything, and the work directory does not eat the group's file quota.

## Run the head job inside a batch job

The Nextflow driver runs for the whole pipeline. Put it in a small, long batch job, not on the login node.
The nf-core documentation for one large LSF site uses 2 cores and 8 GB. Keep the heap near 0.8 x `-M`
(`genomics-jobs`):

```bash
#!/bin/bash
#BSUB -q <long-or-oversubscribed-queue> -n 2 -M 8000 -R "select[mem>=8000] rusage[mem=8000]"
#BSUB -o logs/nf.%J.out -e logs/nf.%J.err
module load <nextflow-module>/<version> <singularity-module>     # pin versions
export NXF_OPTS="-Xms1G -Xmx6G" NXF_ANSI_LOG=false
nextflow run nf-core/<pipeline> -r <release> -profile <site>,singularity \
  -w /path/to/scratch/work -c my.config -with-trace -with-report -resume
```

SLURM: the same with `sbatch --cpus-per-task=2 --mem=8G --time=<days>`.

## Use the institutional profile

If your site has an nf-core/configs profile (`-profile <site>`), use it. It sets the executor, per-job
memory semantics, a submit rate limit and queue selection by time and memory. Read it once
(`nextflow config -profile <site>`) so you know what it does. Site notes: `sites/<site>.md`.

## Resources: start small, retry bigger

- Use the pipeline's labels (`process_single`, `process_low`, `process_medium`, `process_high`,
  `process_long`, `process_high_memory`) and override per process with `withName:` only where the trace
  shows a need.
- Cap with `process.resourceLimits` (Nextflow 24.04 and newer) instead of the old `check_max` helpers.
- Retry on resource exits instead of padding every task:

```groovy
process {
  errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
  maxRetries    = 2
  withName: 'BIG_STEP' { memory = { 6.GB * task.attempt } }
}
```

- For inputs that vary a lot, scale by input size (`memory = { 2.GB + 3.GB * (reads.size() / 1e9) }`), as
  the sanger-tol pipelines do.

## Executor throttles (be a good neighbour)

```groovy
executor {
  queueSize         = 200        // max tasks queued or running at once
  submitRateLimit   = '5 sec'    // or '10/1min'
  pollInterval      = '30 sec'
  queueStatInterval = '2 min'
  killBatchSize     = 50
}
```

## Avoid swarms of tiny tasks

A process that runs for seconds per item should get batched input (`buffer`, `collate`, `groupTuple`) so
each task does minutes of work. Thousands of 10-second jobs cost more in scheduling than in compute.

## Work directory hygiene

- `work/` on Lustre consumes many inodes (several files per task).
- `process.scratch = true` runs each task in node-local temp and copies back only declared outputs.
- After a successful run whose results are published: `nextflow clean -f -k <run-name>` removes that
  run's work files and keeps its log entries. `nextflow clean -f -before <run-name>` removes older runs.
  `cleanup = true` frees space automatically but breaks `-resume`.
- Never put `work/` under a home directory with a small quota.

## Size from the trace

Run with `-with-trace` (fields `name, %cpu, peak_rss, realtime, cpus, memory, exit`). Then:

- `%cpu / (100 x cpus)` below 0.5: lower `cpus` for that process.
- `peak_rss / memory` below 0.4: lower memory, keep retry-with-more for outliers.
- Group by process and use the p95, not the max of one outlier. See `right-size-jobs`.

Cite and link the site config (nf-core/configs, sanger-tol `conf/base.config`) rather than copying it.
More snippets: `references/configs.md`.

## Done means

- The head job ran on a compute node, with pinned Nextflow and container versions.
- The trace has been read and any badly sized process has an override or a note.
- The old `work/` is cleaned, or the user has decided to keep it for `-resume`.
