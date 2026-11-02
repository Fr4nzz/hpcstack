# Example: where a linkage-mapping campaign spent its core-hours

A case for `genomics-jobs` and `right-size-jobs`. A pedigree linkage-mapping project ran Lep-MAP3 on an LSF
cluster with 256-core, 1.9 TB nodes. An audit of all 338 LSF job reports (each `.out` file ends with a
"Resource usage summary") measured where the compute went. Nothing here required new runs: the evidence was
already in the logs.

Effective core use = CPU time / (run time x cores).

## The shape of the waste

For the Lep-MAP3 part of the pipeline (likelihoods, parent calls, filtering, chromosome separation,
ordering):

- **2,264 core-hours allocated, 866 used: 38 % effective use.**
- **774 core-hours (34 %) went to jobs that did not finish successfully**: failed, killed by their owner, or
  killed at the memory limit.
- **Memory reserved: 12,771 GB-hours, against 6,637 GB-hours at peak use**, so about half the reservation
  was never touched.
- Upstream steps sized by hand were fine: read trimming at 90 % and alignment at 82 % effective use. The
  waste concentrated in the long Java steps.

## Findings, worst first

**1. `numThreads=2` used about 1.05 cores.** In all six main ordering runs, a "2-thread" OrderMarkers2 JVM
used 1.03 to 1.10 cores (job CPU-hours divided by the summed wall-hours of the per-LG tasks). The wrapper
ran "cores/2 linkage groups at once, 2 threads each" inside one 8 to 12-core job, so half of every
allocation idled by design. Effective use of those runs: 14 to 44 %. Running one 1-core array task per
linkage group would have cut their allocation from 1,253 to about 401 core-hours (**-68 %**).

**2. The longest linkage group started late and set the wall time.** The wrapper started linkage groups in
number order. In one run the longest LG (14.5 h of ordering) began 7.5 h into a 25.4 h job. In another, a
32 h LG began at hour 11, and the job finished **56 minutes short of the 48 h queue limit**, with 376
core-hours at stake. One array task per LG gives a wall time equal to the longest LG: 25.4 to about 17.9 h,
47.1 to about 36 h, 7.4 to about 3.4 h for the final map.

**3. One failure cost the whole job.** In one run several LGs failed after about an hour. The workflow
engine (without `--keep-going`) then held 12 cores and 80 GB for 25.8 more hours to finish one LG, at 14 %
effective use, and exited with an error. That LG was then ordered again. About 322 core-hours lost.

**4. No `-Xmx`, so the JVM sized its heap for the node.** No script set a Java heap. On a 1.9 TB node the
default maximum heap is about 1/4 of RAM, roughly 480 GB, so the heap grew until LSF killed the job at
`-M`: once at exactly 30.0 GB after 7.7 hours (61.5 core-hours), once after 14 minutes. The response each
time was a bigger request (30, 50, 60, then 80 GB), while the measured need was about 7.5 GB per JVM. JVMs
also showed up to 125 threads in an 8-core job, because GC and JIT threads sized for 256 cores.

**5. Failures were hidden.**
- `LepWrap ... || true` reported every failure (memory kills, incomplete-file errors, a hung interactive
  prompt) as "Successfully completed".
- Downstream steps depended on `-w "ended(name)"`, which fires on failure too, so a crashed chromosome
  could silently drop out of the concatenated input. The dependency was by name, which is ambiguous if the
  pipeline ever runs twice.
- A summary step (`grep likelihood ... | ...` under `set -o pipefail`) failed after all ordering had
  finished, so 6 of 9 parameter-test jobs reported failure. The likelihood was not in the file being
  grepped, so the 3 "successful" jobs printed an empty column.

**6. Replicates never ran.** The method called for several stochastic ordering runs per LG and keeping the
best. The launcher submitted three replicates, only one ever ran, and the selection step had never worked.
As a 1-core array (15 LGs x 3 seeds), three replicates cost about 74 core-hours, close to what one 8-core
job of the threaded design used for a single replicate.

**7. The committed final script no longer parsed.** Inline comments after line-continuation backslashes
(`numMergeIterations=10 \ # comment`) turned `\ ` into an escaped space. `bash -n` reported a syntax error,
and even without it every later argument would have been dropped silently.

**8. Small exploratory jobs with big requests.** Fourteen manual re-splitting jobs asked for 12 to 18 cores
and 16 GB on the long queue. The median run was 1.2 minutes and the peak 0.8 to 2.3 GB.

**9. Two tool builds mixed.** An older build that silently ignores unknown parameters ran most steps, so a
parameter tested for weeks had no effect.

## Savings if the campaign were re-run with the fixes

| Change | Core-hours | Wall time |
|---|---|---|
| per-LG 1-core arrays for ordering, reorder and distances | -850 of 1,253 in the six main runs | production path 25.4 to 17.9 h, final map 7.4 to 3.4 h |
| failure isolation, atomic outputs, resume per LG | -300 to -400 of the 774 lost in failed jobs | no multi-day re-runs |
| `-Xmx` at 0.8 x `-M`, `-M` from the measured peak | -66 (memory-kill re-runs) | memory reservation about -50 %, so less pending |
| working replicates as an array | +15 | same wall time, "best of N" becomes possible |
| LOD sweep as an array instead of serial | about 0 | -2 to -3 h per sweep |
| **Total** | **about 1,200 to 1,300 of 2,264 (about 55 %)** | production path 32.8 to about 21.3 h |

These savings assume that 1-core array tasks start with little pending time and that `numThreads=1` is not
much slower per LG. The 1.03 to 1.10 cores-per-JVM figure strongly suggests this, but it needs one A/B run on
the largest LG to confirm.

## General lessons

1. Measure cores per process before asking for threads. A "2-thread" tool that uses 1.05 cores gets 1 core.
2. One independent unit = one array task. Arrays release cores as tasks finish and isolate failures.
3. The longest unit sets the wall time. Start it first and check it against the queue limit.
4. A many-core job must never wait on one straggler.
5. Always give the JVM a heap that matches the memory limit (`-Xmx` about 0.8 x `-M`) and cap its threads.
6. Size memory from the measured peak plus 20 to 30 %, not from the last kill.
7. Keep compute and summaries in separate jobs.
8. Write outputs atomically and resume per unit.
9. Depend on success (`done()`, `afterok`) on job ids, not on `ended()` or names.
10. Never mask exit codes. No `|| true` and no interactive prompts in batch jobs.
11. Lint job scripts (`bash -n`, `shellcheck`) before every commit.
12. Pin one tool version and check that your parameters took effect.
13. Pass parameters as arguments, not by editing the script. Record them with the git commit in each
    output directory.
14. Replicates need recorded seeds and a selection step that has been tested.
15. Exploration belongs in a small interactive session, not in 18-core batch jobs that last a minute.

Step-by-step guidance for Lep-MAP3: `skills/genomics-jobs/references/lepmap3.md`.
