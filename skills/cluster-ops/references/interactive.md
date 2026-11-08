# Safe interactive sessions

An agent that opens an interactive allocation in tmux and then stops (usage limit, crash, expired login) leaves
the allocation, often a GPU, idle until its walltime. Scheduler idle detection rarely helps: many sites do not
define it on GPU or interactive queues, it only notifies, and its ratio is cumulative over the whole job.

## Three layers

1. **Short walltime** (`-W 2:00`, SLURM `--time=02:00:00`), the backstop if everything else fails. Many sites do
   not let users extend a running job, so start a new session when you need more time.
2. **The wrapper.** Run `scripts/hpc-session` (SLURM `scripts/hpc-session-slurm`) instead of a bare `bash`. Every
   command you send, typed or by `tmux send-keys`, refreshes a heartbeat through `PROMPT_COMMAND`.
3. **The watchdog** inside the wrapper checks about every 60 s and releases the job only when all three hold:
   - the heartbeat is older than `HPC_IDLE_MIN` (default 30)
   - job CPU over the last `HPC_CPU_WINDOW_S` (300 s) is below `HPC_CPU_IDLE_CORES` (0.1 core), read from cgroup
     v2 `cpu.stat`, cgroup v1 `cpuacct.usage`, or `bjobs -o cpu_used` / `sstat` as a fallback
   - the job's GPUs (`CUDA_VISIBLE_DEVICES`) were below `HPC_GPU_IDLE_PCT` (5 %) at every sample in that window.
     A failed `nvidia-smi` counts as busy, and so does another tenant's work on a shared GPU.

Each decision is logged to `~/.hpc-session/<jobid>.log`. If CPU cannot be measured, the watchdog never kills and
the walltime is the only backstop. A GPU pause with low CPU that lasts longer than the CPU window counts as idle,
so raise `HPC_CPU_WINDOW_S` for jobs with long CPU-light pauses (large evaluations, slow uploads). All settings are
listed in the script header (`--help`).

On a new site, run once with `HPC_DRY_RUN=1 HPC_IDLE_MIN=5` and check that the log shows a `cpu_source=v1:` or
`v2:` path.

## Rules

- Closing tmux does not reliably end the job: one GPU idled 2 h 40 min after `exit` plus `kill-session`. Confirm
  the release with the scheduler, as in the SKILL.md ending order. If the job does not resolve, keep the tmux
  session, keep monitoring and report.
- An agent-side stop hook that kills recorded jobs is a bonus, not a guarantee: it does not fire on every stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, and the watchdog releases the GPU 30 minutes later.

## Test record

- Tested on LSF, CPU and GPU queues: the job stayed alive while it used CPU or the user typed, and was released
  about two minutes after the idle limit.
- A GPU-bound job (100 % GPU, near-zero CPU, no typing, 20 s GPU pauses every minute) stayed alive and was
  released about 3 minutes after the GPU work stopped.
- Not yet tested: `hpc-session-slurm`, several GPUs in one job, another tenant on the same GPU, and the
  scheduler's own pre-kill warning.
