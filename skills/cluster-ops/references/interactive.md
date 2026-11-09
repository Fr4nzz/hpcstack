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
and `attach`. Each call names the probe, so nothing depends on a variable from an earlier call. `start` records
the tmux session id, pane id and pane pid, and the job name `hpc-probe-<name>-<random>` with its owner. Every
later send and capture goes to that pane id after checking all three, and `stop` cancels only a job that the
scheduler still lists under that name and owner with the recorded id.

Always read the last line, which starts with `hpc-probe:`. "command N exit R" means R is the command's own code,
even when R is 1, 2, 3, 124 or 125.

| Exit code | Meaning | Next step |
|---|---|---|
| the command's own | `run` or `wait` saw the command's marker | read the printed log tail |
| 2 | refused: bad or used name, missing `-Is`/`--pty` or walltime, last argument not the `hpc-session` executable, own job name, timeout not 1 to 540, probe not ready, previous command still running | fix the call |
| 3 | the tmux session or pane is not the one `start` created (replaced or renamed). Nothing was sent | `stop`, which leaves that session alone |
| 124 | timeout (`start` still pending, or `run` still running) | inspect, then `wait` or `stop` |
| 125 | the session closed or the allocation command exited (rejected, released, walltime, `exit`) | `stop`, which checks the scheduler |
| 1 | `stop` could not confirm that the job ended, so the session stays | keep monitoring and report |

- The job gets the environment and folder of the `start` call, without secret-looking variables (names with
  TOKEN, SECRET, PASSW, KEY, PWD, CREDENTIAL, AUTH or COOKIE): pass a needed token inside the job instead. The
  saved environment file is mode 0600 and is deleted when the launcher reads it, or when `start` fails.
- Each command is written to `hpc-probe-logs/<name>/<n>.sh` and sourced in the job's shell, so `#`, quotes and
  syntax errors cannot hide the marker. Variables, `cd` and `module load` persist between `run` calls. Stdin is
  `/dev/null`, so a prompt fails instead of hanging.
- Output goes to `hpc-probe-logs/<name>/<n>.log` in the run folder, which must be on storage the login node can
  read. State lives in `~/.hpc-probe/<name>.json`.
- `run` and `wait` wait at most 540 s. Set the tool timeout above that (600 s), or pass a `--timeout` below your
  tool's limit. If a call is cut off anyway, `wait` picks it up. A longer step is a batch job.

## Rules

- An agent-side stop hook that kills recorded jobs is a bonus: it does not fire on every stop.

Bad: `bsub -Is -q <gpu-queue> -gpu "num=1" -W 12:00 bash`, then the agent hits its usage limit. 12 GPU-hours idle.
Good: `hpc-session` with `-W 2:00`. The agent stops, and the watchdog releases the GPU 30 minutes later.

## Test record

- Tested on LSF, CPU and GPU queues: the job stayed alive while it used CPU or the user typed, and was released
  about two minutes after the idle limit.
- A GPU-bound job (100 % GPU, near-zero CPU, no typing, 20 s GPU pauses every minute) stayed alive and was
  released about 3 minutes after the GPU work stopped.
- `hpc-probe` in `tests/test_hpc_probe.py` (12 tests): local tmux 3.7 on a private socket whose server was started
  with other settings (another `HPC_PROBE_DIR`, a bare `PATH`), fake `bsub`, `srun`, `bjobs`, `bkill`, `squeue` and
  `scancel` with a job table (id, name, owner, state). A bystander session stays open throughout and receives no
  input. Covered: refused names (empty, `:`, `=`, `.`), timeouts (`nan`, `inf`, `-inf`, 0, 541) and allocations
  (no `-Is` or walltime, decoy `-J hpc-session-x bash`, the wrapper as an argument of `bash`, a wrong or missing
  wrapper, an own job name). Readiness on LSF and SLURM, and a shell on the login host is not ready. Markers per
  command, `#` and an unclosed quote return at once, a timeout gives 124 and blocks the next `run`. A closed
  session or ended allocation gives 125 at once, also for a probe named `by` next to `bystander`. A replacement
  session with the same name and an added active pane receive nothing (3 for the replacement, which `stop`
  leaves alone). Keys typed while a gated job pends, and after its rejection, never run. A reused job id under
  another job name is not killed, a SLURM job listed as `COMPLETED` counts as ended, and a job that outlives its
  shell is killed by its exact id. The environment file is 0600, holds no secret-looking variables, and is gone
  after both a successful and a failed `start`. Two mutations (name targets instead of pane ids, no wrapper
  check) each make a test fail.
- Not yet run with real `bsub -Is` or `srun --pty`, so the readiness echo through a real pending job, the job-name
  lookups (`bjobs -a -u <owner> -J <name>`, `squeue -u <owner> -n <name>`) and Ctrl-C through `bsub` are unchecked.
- Not yet tested: `hpc-session-slurm`, several GPUs in one job, another tenant on the same GPU, and the
  scheduler's own pre-kill warning.
