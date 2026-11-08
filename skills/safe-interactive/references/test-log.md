# hpc-session test log

What `scripts/hpc-session` was tested on, and what is still untested.

## Tested

- **LSF, October 2026.** The wrapper read the job's cgroup CPU counter, kept the job alive while it used CPU
  and while the user typed, and released it about two minutes after the idle limit. Tested on a CPU queue and
  on a GPU queue (idle GPU).
- **LSF, November 2026, busy GPU with idle CPU.** A GPU-bound job (100 % GPU, 0.005 cores CPU, no typing for
  11 min, with 20 s GPU pauses every minute) stayed alive and was released about 3 minutes after the GPU work
  stopped.
- **Busy GPU, shortened settings** (`HPC_IDLE_S=180 HPC_CPU_WINDOW_S=120`): the job survived short GPU pauses
  and was released about 3.5 min after the work stopped.

## Not yet tested

- `hpc-session-slurm`.
- Several GPUs in one job.
- Another tenant on the same GPU.
- The scheduler's own pre-kill warning.

## Incidents behind the rules

- Closing tmux does not reliably end the job: one allocation stayed RUN and idled a GPU for 2 h 40 min after
  `exit` plus `kill-session`, once the agent hit its usage limit.
