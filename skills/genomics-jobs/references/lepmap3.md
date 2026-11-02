# Lep-MAP3 on a batch cluster

Lep-MAP3 is a set of Java programs for linkage mapping. The steps differ a lot in how they parallelise, so
one job for the whole pipeline wastes most of its allocation. Numbers below come from one audited project
(`examples/lepmap3-linkage-map.md`). Re-measure on your own data.

## Steps and how to run them

| Step | Shape | How to run |
|---|---|---|
| Pileup2Likelihoods (from `samtools mpileup`) | per chromosome, I/O and one core | array, one task per chromosome, 1-2 cores, a few GB. Stream chromosomes straight into ParentCall2 instead of writing a concatenated copy |
| ParentCall2 | one pass over all markers | single job, 1-2 cores, small memory |
| Filtering2 | one pass | single job, measure threads |
| SeparateChromosomes2 | multithreaded, one run per LOD limit | array over the LOD values you actually need. It threads well (80-83 % effective use at 18-36 cores), so keep threads per task |
| JoinSingles2All | one pass | single job |
| OrderMarkers2 | per linkage group (LG), long and variable | **array, one task per LG (and per replicate), 1 core**, `-Xmx` set |
| Reorder / distances | per LG | second array, each task depends on its LG |
| Summary and plots | seconds | separate small job, rerunnable |

## `numThreads` is not cores used

In every audited OrderMarkers2 run, `numThreads=2` gave **1.03-1.10 cores** per JVM (job CPU time divided by
the summed wall time of the per-LG tasks). Half of each allocation idled by design.

- Measure once: one large LG with `numThreads=1` and with `numThreads=2`, same seed. Pay for 2 cores only if
  it is close to 2x faster.
- Wrappers (Snakemake-based or a `jobs -r` throttle loop) that run "cores/2 LGs at once with 2 threads each"
  inherit this loss and add another: the allocation stays reserved while the last one or two long LGs run.

## The longest LG sets the wall time

LGs vary several-fold in ordering time (in the audit, from under an hour to 32 h). Wrappers start them in
number order, so a long LG can start hours late. One run finished 56 minutes short of a 48 h queue limit
because its longest LG started at hour 11.

- One array task per LG lets every LG start at once. Wall time becomes the longest LG.
- Check the longest LG against the queue's run limit before submitting.

## Java heap

Lep-MAP3 scripts often call `java` without `-Xmx`. On a big-memory node the default heap is about 1/4 of RAM,
so it grows until the job hits `-M`. The audited project raised `-M` from 30 to 80 GB after kills, while the
measured need was about 7.5 GB per OrderMarkers2 JVM.

```bash
java -Xmx6g -XX:ActiveProcessorCount=1 -cp "$LEPMAP3/bin" OrderMarkers2 \
     data=filtered.gz map=map.txt chromosome="$LG" numThreads=1 > ordered.$LG.tmp
# for replicates, add the seed parameter your build supports (check the program's usage output)
mv ordered.$LG.tmp ordered.$LG
# submit with -n 1 -M 8000, sized from the largest LG
```

## Replicates and selection

OrderMarkers2 is stochastic. If the method says "run N times, keep the best likelihood per LG":

- give each replicate an explicit random seed (check your build's usage output for the parameter) and
  record it
- run replicates as array tasks (`LG x seed`). 15 LGs x 3 seeds at 1 core cost about the same core-hours as
  one 8-core job of the threaded design
- parse the likelihood from the `ordered.N` output (`#*** LG = ... likelihood = ...`), not from a log that
  may not contain it, and test the selection step on real output before the big run.

## Versions and parameters

- Pin one build for every step and print its checksum at job start. Older builds silently ignore parameters
  they don't know, so a parameter sweep can do nothing for weeks.
- After each run, compare the parameters Lep-MAP3 echoes on stderr with the ones you passed. Fail if any are
  missing.
- Build long argument lists in a bash array and comment each element. A comment after a line-continuation
  backslash ends the command there.

## Orchestration traps seen in practice

- `wrapper ... || true` turned memory kills, incomplete-file errors and hung prompts into "Successfully
  completed".
- `-w "ended(name)"` dependencies let a failed chromosome silently drop out of the concatenated input.
- An interactive "choose the map" prompt hung a batch job until its walltime.
- Parameters set by editing the script before `bsub` produced a run labelled with the wrong subsample.
