# Example: a segmentation training twice as fast on a third of the cores

A case for `optimize-pipeline`. An instance-segmentation training (Ultralytics 8.4, YOLO26-seg, 1280 px
tiles, one A100) was going to be trained several times: seeds, ablations, an experiment loop. Each run took
1.09 s per step, needed 16 cores and up to 48 GB of host RAM, and sometimes stalled on a shared GPU.

Result: **1.09 to 0.57 s per step, 18 to 9.8 GB host RAM, 16 to 6 cores, with bit-identical training
samples.** Most of the gain came from two boring causes that profiling found and guessing did not.

## What was done, in order

1. **Pre-registration.** Before any measurement: the fixed recipe (data hash, model, image size, batch,
   epochs, augmentations, library version), the targets (at least 2x per epoch, less RAM, fewer cores), the
   acceptance rule per change class, and an append-only hypothesis log.
2. **Bench.** An interactive GPU allocation, runs of 150 to 900 iterations, metric = median step time after
   the first 30 iterations.
3. **Threads per process** (`ps -L`). Every DataLoader worker had spawned 16 or more BLAS/OpenMP/OpenCV
   threads, sized for the 256-core node. `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1` and
   `cv2.setNumThreads(0)` in the workers: **1.09 to 0.67 s/step**. Class E, identical samples.
4. **GPU ceiling.** Training on a few batches held in memory showed how much the input pipeline could still
   win.
5. **Per-sample CPU profile of one worker** (cProfile over about 300 samples). 60 % of worker time went into
   a library computation whose result is always zero for a single-class dataset, and 19 % into a Python
   loop. Image decoding and mask drawing, the usual suspects, were about 3 % each.
6. **Memory as the scheduler counts it.** The scheduler counted cgroup anonymous RSS, not page cache.
   `memory.stat` and per-process PSS were read before any memory "fix". Runs of 900 or more iterations
   checked for growth.
7. **Silent fallbacks.** On a shared GPU that filled up, the label assigner silently fell back to the CPU
   after a CUDA out-of-memory error, costing 20 to 43 s per step. Chunking it removed the fallbacks.

## Results table (all configurations, including rejected ones)

| Change | Class | Effect | Kept |
|---|---|---|---|
| BLAS/OMP threads = 1 per worker | E | 1.09 to 0.67 s/step | yes |
| skip the always-zero mask computation (single class) | E | -60 % worker CPU | yes |
| vectorised box-from-segment loop | E | -19 % worker CPU | yes |
| chunked label assigner | E | no random CPU fallbacks on a shared GPU | yes |
| `expandable_segments` allocator setting | E | -2 GB GPU memory reserved | yes |
| 5 workers, `-n 6 -M 20000` | W | 16 to 6 cores, 48 to about 10-20 GB | yes |
| GPU augmentation (nvJPEG plus replay) | D | slower than the fixed CPU path | no |
| `torch.compile` | D | -8 %, below the 15 % bar for class D | no |
| `channels_last` | D | broke the optimizer | no |
| packed labels, `gc.freeze`, malloc settings | E | memory worse or unchanged | no |

Exact changes were verified with `np.array_equal`/`torch.equal` on 1,000 to 2,000 real augmented samples,
plus identical losses at a fixed iteration in a short run. No training comparison was needed for them.

## Lessons

- Thread pools sized for the node are the first thing to check on 128 to 256-core machines.
- Profile one worker in a single process. The ranking is rarely what you expect.
- Orphaned DataLoader workers from a killed run kept GPU memory and polluted later measurements. They
  looked like another user's job. Run each probe in its own session and kill the whole session.
- A background status loop in the job script survived the EXIT trap and kept two finished jobs alive for
  about 8 hours. Record helper pids and kill them explicitly.
- If every kept change is exact or scheduling-like, the normal seeds of the real runs already measure the
  remaining noise. A separate equivalence training mostly re-measures worker-count noise.
