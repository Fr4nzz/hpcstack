# Safe interactive sessions

An agent that stops (usage limit, crash, expired login) leaves its interactive allocation, often a GPU, idle until
the walltime. Scheduler idle detection rarely helps: it is often undefined on GPU queues and only notifies.

## Three layers

1. **Short walltime** (`-W 2:00`, SLURM `--time=02:00:00`). Start a new session when you need more time.
2. **The wrapper.** Run `scripts/hpc-session` (SLURM `scripts/hpc-session-slurm`) instead of a bare `bash`. Every
   command, typed or sent with `tmux send-keys`, refreshes a heartbeat through `PROMPT_COMMAND`.
3. **The watchdog** inside the wrapper checks about every 60 s and releases the job only when all three hold:
   - the heartbeat is older than `HPC_IDLE_MIN` (default 30 min)
   - job CPU over the last `HPC_CPU_WINDOW_S` (300 s) is below `HPC_CPU_IDLE_CORES` (0.1 core), read from cgroup
     v2 `cpu.stat`, cgroup v1 `cpuacct.usage`, or `bjobs -o cpu_used` / `sstat` as a fallback
   - the job's GPUs (`CUDA_VISIBLE_DEVICES`) were below `HPC_GPU_IDLE_PCT` (5 %) at every sample in that window.
     A failed `nvidia-smi` counts as busy, and so does another tenant's work on a shared GPU.

Each decision is logged to `~/.hpc-session/<jobid>.log`, so a released job's log says why. If CPU cannot be
measured, the watchdog never kills and the walltime is the only backstop. A GPU pause with low CPU that outlasts
the CPU window counts as idle, so raise `HPC_CPU_WINDOW_S` for jobs with long CPU-light pauses (large evaluations,
slow uploads). The script header (`--help`) lists every setting.

On a new site, run once with `HPC_DRY_RUN=1 HPC_IDLE_MIN=5` and check that the log shows a `cpu_source=v1:` or
`v2:` path.

## Driving the session from an agent

The agent works through the detached tmux session from SKILL.md, one command at a time:

```bash
tmux new -d -s probe                                          # on the login node
tmux send-keys -t probe 'bsub -Is ... hpc-session' Enter
tmux capture-pane -p -t probe -S -200 | tail -5               # repeat until the job's shell prompt shows
tmux send-keys -t probe 'python check.py > probe.log 2>&1; echo "__END_$?"' Enter
tmux capture-pane -p -t probe -S -200 | grep '__END_[0-9]'    # repeat until it matches, then read probe.log
```

- `bsub -Is` prints `<<Waiting for dispatch ...>>` until the job starts. Keys sent before the shell prompt
  appears go to `bsub` or to the running command, not to the shell.
- The `__END_` marker shows that the command finished and carries its exit code. The typed line shows the literal
  `__END_$?`, so only the output matches `__END_[0-9]`.
- The pane holds only recent lines (`-S -200` reads 200 back). Send long output to a log file and read it with
  `tail` or `grep`.
- `tmux send-keys -t probe C-c` interrupts a command that hangs.

## Rules

- If the job does not end after the SKILL.md ending order, keep the tmux session, keep monitoring and report.
- An agent-side stop hook that kills recorded jobs is a bonus: it does not fire on every stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, and the watchdog releases the GPU 30 minutes later.

## Test record

- Tested on LSF, CPU and GPU queues: the job stayed alive while it used CPU or the user typed, and was released
  about two minutes after the idle limit.
- A GPU-bound job (100 % GPU, near-zero CPU, no typing, 20 s GPU pauses every minute) stayed alive and was
  released about 3 minutes after the GPU work stopped.
- The `__END_` marker and `capture-pane` steps work in a local tmux shell. Not yet run inside a cluster job.
- Not yet tested: `hpc-session-slurm`, several GPUs in one job, another tenant on the same GPU, and the
  scheduler's own pre-kill warning.
