---
name: optimize-pipeline
description: Make a slow, memory-hungry or expensive compute step faster and lighter, with output identical or within a pre-registered equivalence test. Use when a cluster job or long local run (training, alignment, variant calling, image processing, inference, ETL) is too slow ("make this faster", "why is this so slow?"), needs too much memory, got OOM-killed or underuses the GPU, or before running the same recipe many times.
---

# Optimize a pipeline

Goal: a measured speed-up or memory cut, with proof that the output did not change (or a pre-registered
test showing it changed acceptably), shipped as the new default.

Skip this for a one-off 30-minute run. Just check thread counts (**Threads per process** below) and move on.

## Budget and stop rule

State a budget before starting (for example, "4 GPU-hours of probes and one working day"). Stop when the
target is met, the budget is spent, or the measured ceiling says there is nothing left to win.
Ask the user before any change of class D or any recipe change.

## Workflow

1. **Pre-register.** Copy `references/hypothesis-log-template.md` into the run folder and fill it in
   before measuring:
   - the baseline recipe that must not change (input hash, tool and library versions, parameters, seeds)
   - the target (for example, 2x per sample, peak RAM under 10 GB, fits in 6 cores)
   - the acceptance rule for each change class (below), with noise floors
   - an append-only hypothesis log: idea, expected effect, written down before it is tested. Wrong guesses
     stay in the log.
2. **Build a test bench, not a production run.** A short representative slice of input, a short
   allocation (`safe-interactive` for interactive probes), and a steady-state metric (median step time after
   warm-up, samples per hour, seconds per item).
3. **Profile before guessing.** In this order:
   - **Threads per process** (`ps -L -p <pid>`, `top -H`). BLAS, OpenMP, MKL and OpenCV size thread pools for
     the whole node (often 128 to 256 cores), so every worker spawns dozens of threads that fight. Set
     `OMP_NUM_THREADS=1` (and `OPENBLAS_`, `MKL_`) per worker, and call `cv2.setNumThreads(0)` in each worker,
     because OpenCV ignores these variables. Check that the tool's threads flag matches
     the cores you asked for, and that a "2-thread" tool really uses 2 cores.
   - **The ceiling**: the same work with I/O removed (data held in memory, one GPU batch repeated). The gap
     between the ceiling and the real rate is all that I/O or loader work can win. No gap, stop there.
   - **Wait versus compute** per step or per item. GPU busy % (`nvidia-smi -l 10`). Cores busy.
   - **Per-stage CPU profile** (`py-spy`, `cProfile` on a few hundred items, `perf`). For a data loader,
     profile one worker in a single process. Rank stages by time. The usual suspects are often not the cause.
   - **I/O pattern**: many small files, metadata calls, compression level, a single `gzip` at the end of a
     fast pipe (`pigz`, `bgzip -@`).
   - **Memory as the scheduler counts it** (cgroup RSS, not page cache). Read `memory.stat` and per-process
     PSS before any memory change. Run long enough to see growth.
   - **Silent fallbacks** in the logs: "falling back to CPU", "retrying", warnings that repeat per step. On
     a shared GPU that fills up, a step can fall back to the CPU after a CUDA out-of-memory error. Lower that step's peak GPU memory (for
     example, process it in chunks) so it stays on the GPU.
4. **Classify every change and verify it the cheap way:**
   - **E, exact**: output identical. Thread counts, skipping work whose result is known, vectorising,
     chunking, prefetch depth, allocator settings. Verify by identity: `cmp`, `md5sum` of sorted output,
     `samtools view | md5sum`, `np.array_equal` on a few thousand items. For training, also check that the loss
     at a fixed iteration of a short run is identical. Keep if it saves at least 5 % time or 10 % memory.
   - **W, scheduling-like**: fewer workers, a different item-to-worker assignment, nondeterministic order.
     Equivalent to a new seed. Verify the output is equivalent up to order or seed noise.
   - **D, distribution-changing**: mixed precision, lossy compression, resized inputs, a different
     algorithm. Keep only with a large gain (at least 15 % time or 25 % memory) AND a pre-registered
     equivalence test that passes (for example, metric within max(2 x seed spread, a fixed floor)).
   - **Recipe changes** (fewer epochs, a smaller model, a different filter) are not pipeline changes. They
     need their own full test and are never folded in silently.
5. **Size the job** from the bench: cores, memory and walltime via `right-size-jobs`.
6. **Ship**: the script's defaults become the accepted settings. Write a results table with every
   configuration tried, including rejected ones, and a short memory note.

## Example of the shape

Bad: "Data loading looks slow. I rewrote it to use GPU augmentation." (No baseline, no class, no proof.)

Good: "Real 1.09 s/step, well above the GPU ceiling. Workers ran 16 or more BLAS threads each. Set them to 1 (class E,
first 2,000 samples identical): 0.67 s/step. Profiling one worker showed 60 % in a computation that is
always zero for this data. Skipped it (class E, identical). With a vectorised loop (E) and fewer workers (W): 0.57 s/step
on 6 cores instead of 16. Gap to ceiling now small. Stop."

## Hygiene

- Kill orphans between measurements. Run each probe in its own session (`setsid`) and `pkill -s <sid>`.
  Workers of a killed parent keep running, hold GPU memory and pollute the next measurement. They can look
  like another user's job.
- Record the pid of every background helper in a job script (status loops, monitors) and kill it
  explicitly. It can outlive the EXIT trap and keep a finished job alive for hours.
- Don't over-test exact changes. If every kept change is class E or W, the normal seeds of the real run
  already measure the remaining noise.
- Record versions. A library upgrade mid-study invalidates the baseline.

## Done means

- The hypothesis log is complete, with results next to predictions.
- Each kept change has its class and its identity or equivalence evidence.
- Before and after numbers come from the same bench, input slice and allocation type.
- The new job request comes from measurements.
