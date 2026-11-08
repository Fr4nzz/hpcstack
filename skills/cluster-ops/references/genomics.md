# Genomics batch work: a short summary

A summary of rules that held on real genomics jobs, not an expert guide. Check tool flags against each tool's own
documentation.

## Shape of the jobs

- **One independent unit, one array task.** Samples, chromosomes, intervals, linkage groups, parameter values and
  seeded replicates go in an array (`-J 'align[1-500]%50'`, line N of a manifest is task N), not in a `&`/`wait`
  loop inside one big job. A failure then costs one unit, and only failed indices are rerun.
- Put the longest unit first in the manifest and check it against the queue's run limit. Units under a minute
  each are batched several per task.
- If a workflow engine must run inside one allocation, set its failure policy so independent units continue after
  one fails, and make the final status list every failed unit.
- **Threads flag equals cores**: `bwa mem -t N`, `minimap2 -t N`, `samtools sort -@ (N-1)`, `pigz -p N`, with
  `span[hosts=1]`, then measure the cores really used. `samtools sort -m` is per thread, so `-@` x `-m` stays
  below `-M`.
- **Java heap about 0.8 x the memory limit.** Without `-Xmx` the JVM takes about 1/4 of the node's RAM and grows
  until the scheduler kills it. Add `-XX:ActiveProcessorCount=$NCPU` and `-Djava.io.tmpdir=$TMPDIR`. Launchers
  take them through `--java-options` or `JAVA_OPTS`.
- **No small-file floods.** One bgzipped, indexed file per sample or chromosome (`bgzip`, `tabix`,
  `samtools index`), never one per locus. Temp files on node-local disk. Merge array outputs in a small dependent
  job. Reuse shared reference data and indexes instead of copying them per job, and pin container images by
  digest or file hash.

## Reads to linkage map

One project went from raw reads through alignment, a VCF and QC to a Lep-MAP3 linkage map for a pedigree. The
Lep-MAP3 numbers below were measured there. Re-measure on your own data.

- Align per sample as an array, sorting and indexing in the same task.
- Where the variant caller supports independent intervals or chromosomes, run them as an array and merge the
  outputs. Keep QC as its own step, sized from a measurement, and record its filter thresholds beside the output.
- Lep-MAP3 steps differ in shape, so one job for the whole pipeline wastes most of its allocation:
  - per-chromosome likelihoods: an array, 1 to 2 cores and a few GB per task
  - SeparateChromosomes2 threads well (about 80 % effective at 18 to 36 cores): keep its threads, one task per
    LOD limit you need
  - OrderMarkers2: one 1-core task per linkage group and seed. `numThreads=2` gave about 1.05 cores per JVM in
    every measured run, so time one large group with 1 and 2 threads before paying for 2. With every group
    started at once, the longest group sets the wall time
  - single-pass steps (ParentCall2, Filtering2, JoinSingles2All): single small jobs
- Give each replicate an explicit seed, recorded beside the output. Parse the likelihood from the output file
  itself, and test the "keep the best" step on real output before the big run.
- Pin one tool build for every step. Some builds silently ignore parameters they do not know, so compare the
  parameters the tool echoes with the ones you passed.
- Answer interactive prompts in advance or skip them: in a batch job they hang until the walltime.
- Summaries and plots run in a separate small job, so a plotting bug cannot fail hours of compute.
- Exploration (re-splitting, plot code) belongs in a short interactive session, not in a series of many-core
  batch jobs that each last a minute.
