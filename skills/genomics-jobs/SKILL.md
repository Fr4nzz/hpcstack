---
name: genomics-jobs
description: Run bioinformatics workloads (alignment, sorting, variant calling, linkage mapping, Java tools, per-sample or per-chromosome steps) efficiently on a shared cluster. Use when the user says "run bwa on 500 samples", "align these reads", "call variants per chromosome", "run GATK", "Lep-MAP3", "picard", "the Java job got killed", "submit one job per sample", or writes a loop that submits many similar jobs.
---

# Genomics jobs

Goal: every independent unit runs as its own right-sized task, threads match cores, Java heaps match the
memory limit, failures cost one unit, and the output is a few indexed files, not a flood of small ones.

## One independent unit = one array task

Samples, chromosomes, linkage groups, intervals, parameter values and replicates belong in a job array, not
in a `&`/`wait` loop or a workflow engine inside one big allocation.

```bash
# manifest.tsv: one line per unit. Line N is task N.
bsub -J 'align[1-500]%50' -n 4 -M 8000 -R "select[mem>=8000] rusage[mem=8000] span[hosts=1]" -W 3:00 \
     -o logs/%J.%I.out bash align.sh manifest.tsv
# in align.sh:  read -r sample r1 r2 < <(sed -n "${LSB_JOBINDEX}p" "$1")
```

Arrays release cores as tasks finish, isolate failures, and let the longest unit start straight away. Rerun
only failed indices (`bsub -J 'align[7,42,311]'`). Patterns and SLURM syntax: `references/arrays.md`.

Bad: one 8-core job running 15 chromosomes 4 at a time. The slowest chromosome starts late and sets the
wall time, and the whole allocation waits on one straggler.
Good: a 15-task array with 1 core each. The longest task starts at once. Total wall time = longest unit.

## Threads must match cores

| Tool | Threads flag | Note |
|---|---|---|
| bwa / bwa-mem2 | `-t N` | |
| minimap2 | `-t N` | |
| samtools sort/view | `-@ (N-1)` | extra threads beyond the main one |
| samtools sort memory | `-m` | **per thread**: `-@` x `-m` must stay below `-M`. Use `-T $TMPDIR/x` |
| GATK | `--native-pair-hmm-threads N` | plus Java settings below |
| FastQC | `-t N` | one thread per file by default |
| pigz / bgzip | `-p N` / `-@ N` | replace a lone `gzip` at the end of a fast pipe |
| Java tools in general | tool-specific `numThreads=` etc. | **measure**: requested threads are not used cores |

Watch for tools that default to every core on the node (often 128 to 256). Always pair with
`span[hosts=1]` (SLURM `--nodes=1`). Then measure with `right-size-jobs`: a "2-thread" tool that uses 1.05
cores gets 1 core.

## Java: heap versus the memory limit

- Without `-Xmx` the JVM's default maximum heap is about 1/4 of the node's RAM. On a 1-2 TB node that is
  hundreds of GB, so the heap grows until the scheduler kills the job (`TERM_MEMLIMIT`). Raising `-M` after
  each kill only makes the job pend longer.
- Set `-Xmx` to about 0.8 x `-M`. The JVM also needs metaspace, thread stacks and native buffers.
- Cap JVM threads to the allocation: `-XX:ActiveProcessorCount=$NCPU` (or `-XX:ParallelGCThreads=`).
- `-Djava.io.tmpdir=$TMPDIR` so temp files go to node-local disk.
Detail and a wrapper: `references/java-memory.md`.

## Files: few, indexed, node-local while working

- Stream to one bgzipped, indexed file (`bgzip`, `tabix`, `samtools index`). One output per sample or
  chromosome, not per locus.
- Merge array outputs at the end in a small dependent job.
- Temp files on node-local disk (`$TMPDIR`), checked with `findmnt -T`.
- Reuse shared reference data and indexes. Don't copy references per user or per job.
- Containers: `singularity exec --bind <data>:<data> image.sif cmd`. Pin the image by digest or file hash.

## Orchestration that does not lie

- Depend on success: `-w "done(<jobid>)"` (SLURM `afterok`), never `ended()`/`afterany`, never on reused
  job names.
- Never `cmd || true` around the real work, and no interactive prompts in batch jobs.
- Write `x.tmp` then `mv x.tmp x`. A restart skips finished units and never trusts truncated files.
- Keep summary steps (grep, awk, plots) in a separate cheap job, so a summary bug cannot fail hours of
  compute.
- Pass parameters as arguments or `-env`, not by editing the script. Write the parameters, the git commit
  and the tool version into each output directory.
- Pin one tool version. Some tools ignore unknown parameters silently: check the tool's echoed parameter
  list against what you passed.
- Lint job scripts with `bash -n` and `shellcheck` before submitting. A comment after a line-continuation
  backslash silently drops every later argument.
- Exploration (re-splitting, trying plot code) belongs in a small interactive session (`safe-interactive`),
  not a series of 18-core batch jobs that last a minute.

## Lep-MAP3 and linkage mapping

Step-by-step guidance (which steps parallelise per linkage group, `numThreads`, heap sizing, replicates
with seeds) is in `references/lepmap3.md`. The measured case is `examples/lepmap3-linkage-map.md`.

## Done means

- Each unit is an array task with measured cores and memory. Failed indices are listed and rerun.
- Java jobs have `-Xmx` and a thread cap. No unexplained `TERM_MEMLIMIT`.
- Outputs are indexed, merged, checksummed, with parameters and versions recorded beside them.
