---
name: cluster-autoresearch
description: Run an autonomous, budgeted experiment loop (propose, run, keep or discard, repeat) on a shared cluster. Use when the user says "run experiments overnight", "autoresearch", "try ideas until it improves", "sweep this", "keep going while I'm away", or wants many model, parameter or pipeline variants tested against a fixed metric.
---

# Cluster autoresearch

Goal: find changes that beat the incumbent on a fixed metric, within a stated compute budget, with every
idea and result on record, and without leaving idle allocations behind.

This adapts the karpathy/autoresearch loop to shared hardware. The big difference: **there is no "never
stop".** The loop has a budget and a stop rule, and each wave is sized so other users still get a GPU.

## Before the first wave (needs the user's OK)

Write `program.md` from `references/program-template.md`. It fixes:

- **Metric and data split.** One primary metric. A development split for decisions. Locked test sets are
  opened only by an explicit, logged `confirm` step that the user approves.
- **Fixed files**, SHA-256 pinned: evaluation, data access, the keep rule. **One editable file** (the model,
  config or pipeline step). If a pinned hash changes, stop and report.
- **Keep rule**: gain > max(2 x seed spread, a floor), measured over the same seeds as the incumbent, plus
  guard metrics that must not drop and a simplicity rule (equal score with less code or compute wins).
- **Seeds must really differ.** Some frameworks seed the data loader with a constant, and a seed flag then
  changes only newly built layers: two fine-tunes from one checkpoint train identically and the seed spread is
  zero. Check that seed 1's first logged losses differ from seed 0's before submitting more seeds. Two
  identical "replicates" once cost 2 GPU-h and left a result with one effective seed.
- **Budget**: total GPU-hours or core-hours, a wall-clock end, and per-wave size. State the estimate:
  "8 runs x 0.5 GPU-h = 4 GPU-h per wave, at most 5 waves".
- **Stop rule**: budget spent, wall-clock end reached, or k waves in a row without a keep (default k = 2).

## The loop

1. **Register ideas before running them.** One line each in `ideas.jsonl` or the log: hypothesis, expected
   gain, falsifier, cost. Park ideas whose expected gain is below the noise floor. Critique the batch once
   before spending compute on it.
2. **Run a wave** of 8 to 12 experiments through the file queue (below). Cap concurrency.
3. **Record every result** in `results.tsv` (`scripts/results_tsv.py add ...`): id, metric, cost, status
   (`keep`, `discard`, `crash`), description. Crashes are results too.
4. **Decide** with the keep rule, nothing else. A kept change becomes the new incumbent and is replicated
   with fresh seeds before the next wave builds on it.
5. **Write 2 to 5 lines in `FINDINGS.md`** per wave: what moved, what didn't, what you'll try next and why.
6. **Plot** `scripts/plot_progress.py results.tsv -o progress.png` (grey discarded, green kept, running-best
   step line).
7. Check the stop rule. Continue or stop and report.

## Running experiments: a worker job, not an interactive shell

Experiments go into a file queue. A batch job drains it and exits on its own.

```bash
scripts/queue_worker.py add --queue q/ --id w03_lr2e4 -- python train.py --lr 2e-4 --seed 1
bsub -q <gpu-queue> -n 4 -M 16000 -gpu "num=1" -W 8:00 -o q/worker.%J.out \
     python scripts/queue_worker.py run --queue q/ --idle-min 25 --max-hours 7.5
```

- If the agent dies, the worker finishes the queue and exits after 25 idle minutes. No allocation idles until
  its walltime.
- `touch q/STOP` makes workers finish their current task and exit.
- The worker exits 0 when it stops normally (idle, max-hours, STOP), even if tasks failed: read
  `q/results/`. A signal (`bkill`, the `-wa USR2` walltime warning) stops the running task, records it as
  `interrupted` and exits 128+N, so a `done()` dependent does not run on a half-drained queue.
- For several workers, submit an array with a cap (`-J 'w[1-4]%4'`). Workers claim tasks with a file lock.
- Results land in `q/results/<id>.json` (exit code, runtime) and logs in `q/logs/<id>.log`.
- Each task checkpoints per unit, so a killed or slow worker redoes one task, not the wave. A task whose
  worker host is overloaded can be killed and requeued once, and the requeue is recorded.

## Repurposable slot (UNTESTED on a cluster)

When the queue is long, a running worker is worth more than a new submission. Tested on synthetic tasks only.

```bash
scripts/queue_worker.py add --queue q/ --id w04_full --est-min 240 \
    --resume-cmd "python train.py --cfg full.yaml resume=last.pt" -- python train.py --cfg full.yaml
scripts/queue_worker.py add --queue q/ --id w04_ablate --priority high --est-min 40 -- python train.py --cfg ablate.yaml
scripts/queue_worker.py preempt --queue q/ --task w04_full --reason "ablation decides whether the next wave runs"
```

- Long trainings that may be displaced go in as worker tasks with a `--resume-cmd`, not as bare batch jobs.
  A bare batch job cannot take new work, a worker can.
- Prefer queueing into your running worker over a new submission when the queue is long. Preempt only with
  a recorded reason that the new work is worth more.
- `preempt` writes `q/preempt/<id>.json` and signals nothing. Within about 10 s the worker sends the task
  `--preempt-signal` (default TERM), waits `--preempt-grace` (120 s), then KILLs it. The task is recorded as
  `preempted`, logged in `q/preempt.log`, and re-queued ahead of other normal tasks with its `--resume-cmd`.
  The high-priority task runs next in the same allocation.
- The preempted run loses the work since its last checkpoint, for example up to one epoch when the trainer
  resumes from its last-epoch checkpoint. Without `--resume-cmd` it restarts from scratch.
- A task is preempted at most twice. `preempt` prints the worker's time left and warns when a high task's
  `--est-min` does not fit. `bkill` or USR2 on the worker still stops everything.
- Fair use: your own queue and jobs only, no idle allocations held, the idle exit stays on.

## Attribution

When several ideas are stacked, find out which ones carried the gain before reporting. Ablate the final
stack (remove one idea at a time) or reorder the main line. "A smaller model was better" may really be "a
fine-tuned model was better". Test the confound before writing the conclusion. See
`examples/autoresearch-classifier.md`.

## Fair use

- Waves of 8 to 12 jobs, an array cap, shared GPUs unless exclusive is measured to be needed.
- State GPU-hours before each wave. Three or more GPUs at once needs a reason in `FINDINGS.md`.
- Cheap proxy runs (fewer epochs, a data slice) for screening, full runs only for candidates, if the proxy
  has been shown to rank ideas the same way as full runs.

## Done means

- `results.tsv`, `progress.png` and `FINDINGS.md` are complete and agree with each other.
- The incumbent is replicated with fresh seeds. Locked-set confirmations, if any, are logged.
- The budget used is reported against the budget stated. No workers or allocations are left running.
