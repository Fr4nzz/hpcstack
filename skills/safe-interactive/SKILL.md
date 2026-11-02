---
name: safe-interactive
description: Open interactive cluster allocations (CPU or GPU shells) that release themselves when the agent stops. Use when the user says "give me a GPU shell", "get an interactive node", "bsub -Is", "srun --pty", "salloc", "I want to debug on a compute node", or when you are about to open any interactive allocation, especially from inside tmux.
---

# Safe interactive allocations

> **Tested on LSF, not yet on SLURM.** On an LSF cluster (October 2026) `scripts/hpc-session` read the job's
> cgroup CPU counter, kept the job alive while it used CPU and while the user typed, and released it about
> two minutes after the idle limit, on a CPU queue and on a GPU queue (idle GPU). Not yet tested: a busy GPU
> with idle CPU, and `hpc-session-slurm`. On a new site, first run once with `HPC_DRY_RUN=1 HPC_IDLE_MIN=5`
> and check `~/.hpc-session/<jobid>.log` shows a `cpu_source=v1:` or `v2:` path.

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
   fails. Many sites do not let users extend a running job (`bmod -W` or `scontrol update`), so choose the
   walltime up front and start a new session if you need more. Test once with a `sleep` job if unsure.
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
- End in this order. (1) `exit` the shell inside the allocation. (2) Confirm with `bjobs`/`squeue` that the
  allocation is gone. If it is still running after 30 s, check the id is yours, kill that exact id and check
  the reply. (3) Only then close that one tmux session (`tmux kill-session -t <name>`). Closing tmux does not
  reliably end the job: one allocation stayed RUN and idled a GPU for 2 h 40 min after `exit` plus
  `kill-session`, once the agent hit its usage limit.
- Never `tmux kill-server` on a shared login node. It ends every session you have there, including other
  agents' live ones. Close stale sessions one by one after checking that each pane is an idle shell with no
  child processes. Idle sessions pile up: one user had 36 left over from a single month.
- Ask for what a probe needs: 1 GPU, a few cores, memory from the last measurement. Use a CPU interactive
  queue for CPU-only work rather than holding a GPU.
- When the probes end, release the allocation as above. The real run goes in as a batch job (`cluster-ops`).
- Never run a watchdog daemon on the login node or from cron. It belongs inside the allocation.
- An agent-side stop hook that kills recorded jobs is a bonus, not a guarantee. It does not fire on every
  kind of stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, the watchdog releases the GPU 30 minutes later.

## Done means

- The allocation is gone, as shown by the scheduler (`bjobs`/`squeue`), not by closing the terminal, or it
  is still in active, logged use.
- If the watchdog released it, the log says why.
