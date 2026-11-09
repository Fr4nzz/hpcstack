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

`scripts/hpc-probe` (`--help` lists every option) gives one tool call per action: `start`, `run`, `wait`, `stop`
and `attach`. Each call names the probe, so nothing depends on a variable from an earlier call.

| Exit code | Meaning | Next step |
|---|---|---|
| the command's own | `run` or `wait` saw the command's marker | read the printed log tail |
| 2 | refused: bad or used name, missing `-Is`/`--pty`, walltime or `hpc-session`, probe not ready, previous command still running | fix the call |
| 124 | timeout (`start` still pending, or `run` still running) | inspect, then `wait` or `stop` |
| 125 | the session closed or the allocation command exited (rejected, released, walltime, `exit`) | `stop`, which checks the scheduler |
| 1 | `stop` could not confirm that the job ended, so the session stays | keep monitoring and report |

- The job gets the environment (without secret-looking variables such as tokens, keys and passwords) and folder of
  the `start` call. Variables and `module load` persist between
  `run` calls. Each command reads stdin from `/dev/null`, so a prompt fails instead of hanging.
- Output goes to `hpc-probe-logs/<name>/<n>.log` in the run folder, which must be on storage the login node can
  read. State lives in `~/.hpc-probe/<name>.json`.
- `run` and `wait` wait at most 540 s, below a foreground tool timeout of about 600 s. A longer step is a batch job.

## Rules

- An agent-side stop hook that kills recorded jobs is a bonus: it does not fire on every stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, and the watchdog releases the GPU 30 minutes later.

## Test record

- Tested on LSF, CPU and GPU queues: the job stayed alive while it used CPU or the user typed, and was released
  about two minutes after the idle limit.
- A GPU-bound job (100 % GPU, near-zero CPU, no typing, 20 s GPU pauses every minute) stayed alive and was
  released about 3 minutes after the GPU work stopped.
- `hpc-probe` in `tests/test_hpc_probe.py`: local tmux 3.7 on a private socket, fake `bsub`, `srun`, `bjobs`,
  `bkill`, `squeue` and `scancel`, Python 3.14 and 3.8. A bystander session stays open the whole time and receives
  no input. Covered: empty, malformed (`:`, `=`, `.`) and unknown names are refused, and so are allocations without
  `-Is`, walltime or `hpc-session`. LSF and SLURM readiness give the job id and the fake compute host, and a shell
  that reports the login host is not marked ready. A command waits for its own marker while older markers are on
  screen, a timeout returns 124 and blocks the next `run` until `wait` sees the end, a closed session or an exited
  allocation returns 125 at once, and keys typed while a rejected job pended or after the rejection never run.
  `stop` ends with `exit` alone when that is enough and kills the exact id when the job outlives its shell.
- Not yet run with real `bsub -Is` or `srun --pty`, so the readiness echo through a real pending job, the
  `bjobs`/`squeue` parsing and Ctrl-C through `bsub` are unchecked.
- Not yet tested: `hpc-session-slurm`, several GPUs in one job, another tenant on the same GPU, and the
  scheduler's own pre-kill warning.
