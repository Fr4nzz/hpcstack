# Nextflow config snippets

Sources worth reading (link, don't copy):

- nf-core/configs: <https://github.com/nf-core/configs> (`conf/<site>.config`, `docs/<site>.md`)
- Nextflow LSF executor: <https://www.nextflow.io/docs/latest/executor.html#lsf>
- Nextflow SLURM executor: <https://www.nextflow.io/docs/latest/executor.html#slurm>
- sanger-tol pipelines' `conf/base.config`, for resource scaling by input size and retry rules:
  <https://github.com/sanger-tol>

## LSF executor

```groovy
executor {
  name            = 'lsf'
  perJobMemLimit  = true     // LSF -M is per job on this site (check LSB_JOB_MEMLIMIT / your site profile)
  queueSize       = 200
  submitRateLimit = '5 sec'
  killBatchSize   = 50
}
process {
  executor       = 'lsf'
  clusterOptions = { "-R 'select[mem>=${task.memory.toMega()}] rusage[mem=${task.memory.toMega()}] span[hosts=1]'" }
}
```

Without `perJobMemLimit` (or `perTaskReserve`) Nextflow divides memory by cores, matching sites where `-M`
is per core. Getting this wrong halves or multiplies every request.

## Queue by time and memory

```groovy
process.queue = {
  task.memory > 350.GB ? 'hugemem' :
  task.time   > 2.d    ? 'week'    :
  task.time   > 12.h   ? 'long'    : 'normal'
}
```

Use your site's real queue names and limits (`bqueues -l`).

## Retry with more resources

```groovy
process {
  errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
  maxRetries    = 2
  memory        = { 4.GB * task.attempt }
  time          = { 4.h  * task.attempt }
  resourceLimits = [cpus: 64, memory: 500.GB, time: 7.d]
}
```

## Node-local scratch

```groovy
process.scratch = true            // or '$TMPDIR' if your site sets a node-local TMPDIR
process.stageInMode = 'symlink'
```

## Trace and report

```groovy
trace {
  enabled = true
  file    = "pipeline_info/trace_${new Date().format('yyyyMMdd_HHmmss')}.txt"
  fields  = 'task_id,name,status,exit,cpus,%cpu,memory,peak_rss,time,realtime,attempt'
}
report.enabled = true
```

## Container cache

```groovy
singularity {
  enabled    = true
  autoMounts = true
  cacheDir   = '/path/to/shared/singularity-cache'   // one shared cache per group, not per user
}
```
