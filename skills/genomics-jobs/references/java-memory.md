# Java memory and threads under a scheduler

## Why Java jobs die at the memory limit

The JVM picks its default maximum heap from the machine, not from your job: usually 1/4 of physical RAM.
Container-aware JVMs (Java 10 and newer) read the cgroup memory limit when one is set on the cgroup, but
many LSF and SLURM setups enforce memory by monitoring instead, so the JVM still sees the whole node. On a
1.9 TB node the default heap is about 480 GB. The garbage collector has no reason to work hard, the heap
grows, and the scheduler kills the job at `-M`.

Signs: `TERM_MEMLIMIT` or exit 130/137 at exactly the requested memory, after hours of normal progress, and a
peak equal to the limit every time you raise it.

## Settings

| Setting | Value | Why |
|---|---|---|
| `-Xmx` | about 0.8 x `-M` | room for metaspace, thread stacks, code cache, native buffers |
| `-Xms` | small, or equal to `-Xmx` for long jobs | equal avoids resizing, small avoids reserving early |
| `-XX:ActiveProcessorCount=N` | cores granted | GC and JIT threads otherwise size for 256 cores |
| `-XX:ParallelGCThreads=N` | as above, if the JVM is old | |
| `-Djava.io.tmpdir=$TMPDIR` | node-local | |
| `-XX:+ExitOnOutOfMemoryError` | on | fail fast with a clear message instead of thrashing |

## Wrapper

```bash
JOB_MEM_MB=${JOB_MEM_MB:-8000}     # pass the -M value in yourself (e.g. bsub -env "all,JOB_MEM_MB=8000")
NCPU=${LSB_DJOB_NUMPROC:-${SLURM_CPUS_PER_TASK:-1}}
XMX=$(( JOB_MEM_MB * 8 / 10 ))
JAVA_OPTS="-Xmx${XMX}m -XX:ActiveProcessorCount=$NCPU -XX:+ExitOnOutOfMemoryError -Djava.io.tmpdir=${TMPDIR:-/tmp}"
java $JAVA_OPTS -cp "$TOOL_JAR" Main args...
```

For tools with a launcher script (GATK, picard wrappers, nextflow), pass the same options through the
launcher's variable (`--java-options`, `JAVA_OPTS`, `_JAVA_OPTIONS`, `NXF_OPTS`).

## Sizing

1. Run the largest unit once with a generous `-M` and `-Xmx` at 0.8 of it. Record the peak RSS.
2. Set `-M` to 1.3 x that peak, `-Xmx` to 0.8 x the new `-M`. Check that the heap was not the binding limit
   (GC logs or the tool's own memory report).
3. Size the array from the largest unit, or scale per unit by input size.
