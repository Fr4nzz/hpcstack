# Genomics batch work: a short summary

A summary of rules that held on real genomics jobs, not an expert guide. Check tool flags against each tool's own
documentation.

## Shape of the jobs

- **One independent unit, one array task.** Samples, chromosomes, linkage groups and seeded replicates go in an
  array (`-J 'align[1-500]%50'`, line N of a manifest is task N), not in a `&`/`wait` loop inside one big job.
  A failure then costs one unit, and only failed indices are rerun.
- Put the longest unit first in the manifest and check it against the queue's run limit. Units under a minute
  each are batched several per task.
- **Threads flag equals cores**: `bwa mem -t N`, `minimap2 -t N`, `samtools sort -@ (N-1)`, `pigz -p N`, with
  `span[hosts=1]`. Then measure: a "2-thread" tool that uses 1.05 cores gets 1 core. `samtools sort -m` is per
  thread, so `-@` x `-m` stays below `-M`.
- **Java heap about 0.8 x the memory limit.** Without `-Xmx` the JVM takes about 1/4 of the node's RAM and grows
  until the scheduler kills it. Add `-XX:ActiveProcessorCount=$NCPU` and `-Djava.io.tmpdir=$TMPDIR`. Launchers take
  them through `--java-options` or `JAVA_OPTS`.
- **No small-file floods.** One bgzipped, indexed file per sample or chromosome (`bgzip`, `tabix`,
  `samtools index`), never one per locus. Temp files on node-local disk. Merge array outputs in a small dependent
  job.

## Reads to linkage map

From a pedigree recombination study: raw reads, alignment, a VCF, QC, then a linkage map with Lep-MAP3.

- Align per sample as an array (sort and index in the same task), call per chromosome or interval as an array,
  then merge. Run QC on the merged VCF in its own cheap job, and keep the filter thresholds with the output.
- Steps differ in shape, so one job for the whole pipeline wastes most of its allocation. Per-chromosome and
  per-linkage-group steps are arrays of 1-core tasks. Single-pass steps are single jobs.
- The longest linkage group sets the wall time (groups varied from under an hour to 32 h). One task per group
  lets them all start at once.
- `numThreads=2` gave about 1.05 cores per JVM in every measured ordering run. Time one large group with 1 and 2
  threads before paying for 2 cores.
- Stochastic steps get an explicit seed per replicate, recorded beside the output, and run as tasks (unit x seed).
  Parse the likelihood from the output file itself, and test the "keep the best" step on real output first.
- Pin one tool build for every step. Some builds silently ignore parameters they do not know, so compare the
  parameters the tool echoes with the ones you passed.
- Depend on `done(<jobid>)`, never on `ended()` or a reused name, so a failed chromosome cannot drop out of the
  next step. Never wrap the real work in `|| true`, and answer interactive prompts in advance (in a batch job they
  hang until the walltime).
- Summaries and plots run in a separate small job, so a plotting bug cannot fail hours of compute.
- Exploration (re-splitting, plot code) belongs in a short interactive session, not in large one-minute jobs.
