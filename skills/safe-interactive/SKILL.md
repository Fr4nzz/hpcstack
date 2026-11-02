---
name: safe-interactive
description: Open interactive cluster allocations (CPU or GPU shells) that release themselves when the agent stops. Use when the user says "give me a GPU shell", "get an interactive node", "bsub -Is", "srun --pty", "salloc", "I want to debug on a compute node", or when you are about to open any interactive allocation, especially from inside tmux.
---

# Safe interactive allocations

> **UNTESTED on a real cluster: validate on a CPU queue first.** `scripts/hpc-session` has passed local
> smoke tests with a fake `bkill` (idle, busy, fresh heartbeat, CPU fallback via `bjobs`), but not on a
> real LSF or SLURM job. Before relying on it: run it on a CPU queue with `HPC_DRY_RUN=1 HPC_IDLE_MIN=5`,
> check `~/.hpc-session/<jobid>.log` shows the right CPU source and decisions, then repeat on a GPU queue.

Problem: an agent opens an interactive allocation in tmux, then stops (usage limit, crash, laptop sleep,
expired login). The allocation, often a GPU, idles until its walltime. Scheduler idle detection rarely
helps: many sites don't define it on GPU or interactive queues, it only notifies, and its ratio is
cumulative over the whole job.

Goal: no interactive allocation outlives its usefulness by more than about 30 minutes.

## Decision

1. **Can the probe be scripted?** Then don't go interactive. Use a batch job, or a worker that drains a file
   queue and exits when idle (`cluster-autoresearch/scripts/queue_worker.py run --idle-min 25`).
2. **Genuinely interactive** (first-run debugging, REPL work): use all three layers below.

## The three layers

1. **Short walltime.** `-W 2:00` (SLURM `--time=02:00:00`). This is the hard backstop if everything else
   fails. On LSF, extend with `bmod -W <new> <jobid>` when a session legitimately runs longer, if the site
   allows it (`LSB_MOD_ALL_JOBS=Y`). Test that once. SLURM users usually cannot extend.
2. **The wrapper.** Start `hpc-session` instead of a bare `bash`:

   ```bash
   tmux new -s probe      # on the login node
   bsub -Is -q <queue> -n 2 -M 4000 -R "select[mem>=4000] rusage[mem=4000]" -W 2:00 -wa USR2 -wt 10 \
        /path/to/hpcstack/skills/safe-interactive/scripts/hpc-session
   # SLURM:
   srun --cpus-per-task=2 --mem=4G --time=02:00:00 --pty /path/to/scripts/hpc-session-slurm
   ```
3. **The watchdog** inside the wrapper. Every command you send (typed or `tmux send-keys`) refreshes a
   heartbeat through `PROMPT_COMMAND`, so no extra discipline is needed. Every 60 s it checks, and releases
   the job only when **all three** hold:
   - the heartbeat is older than `HPC_IDLE_MIN` (default 30)
   - job CPU over the last 5 minutes is below 0.1 core (cgroup v2 `cpu.stat`, cgroup v1 `cpuacct.usage`,
     or `bjobs -o cpu_used` / `sstat` as a fallback);
   - every visible GPU is below 5 % utilisation.

   A long training started inside the shell keeps the job alive while it computes. When it ends and nobody
   types, the job goes after `HPC_IDLE_MIN`. Each decision is logged to `~/.hpc-session/<jobid>.log`.
   If CPU cannot be measured, the watchdog never kills and the walltime is the only backstop.

Settings are environment variables, listed in the script header (`HPC_IDLE_MIN`, `HPC_CPU_IDLE_CORES`,
`HPC_GPU_IDLE_PCT`, `HPC_DRY_RUN`, ...).

## Rules

- An interactive allocation without `hpc-session` and a short walltime is a bug. Fix it before continuing.
- Record the job id, tmux session name and purpose where the user can see it (status file or the chat).
- Ask for what a probe needs: 1 GPU, a few cores, memory from the last measurement. Use a CPU interactive
  queue for CPU-only work rather than holding a GPU.
- When the probes end, exit the shell. The real run goes in as a batch job (`cluster-ops`).
- Never run a watchdog daemon on the login node or from cron. It belongs inside the allocation.
- An agent-side stop hook that kills recorded jobs is a bonus, not a guarantee. It does not fire on every
  kind of stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, the watchdog releases the GPU 30 minutes later.

## Done means

- The allocation is gone (`bjobs`/`squeue` shows nothing for it), or it is still in active, logged use.
- If the watchdog released it, the log says why.
