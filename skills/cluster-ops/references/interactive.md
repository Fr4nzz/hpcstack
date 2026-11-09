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

Each step is a separate tool call. Write the printed session name into every later one (here `probe-1108-143005`).

```bash
# 1. A new session whose command is the submission itself. tmux refuses a name in use: then pick another.
S=probe-$(date +%m%d-%H%M%S); echo "$S"
tmux new -d -s "$S" -c "$PWD" -x 250 'bsub -Is ... hpc-session; echo "__ALLOC_ENDED_$?"; sleep 600' || exit 1

# 2. Wait for the start, then prove the shell is in the job.
S=probe-1108-143005
timeout 3600 bash -c 'until tmux capture-pane -p -J -t "=$0:" -S -200 |
  grep -Eq "^<<Starting on|^__ALLOC_ENDED_"; do sleep 10; done' "$S"
tmux send-keys -t "=$S:" 'echo "__READY_${LSB_JOBID:-none}_$(hostname)"' Enter
timeout 60 bash -c 'until tmux capture-pane -p -J -t "=$0:" -S -200 |
  grep -Eqx "__READY_[0-9]+_.+|__ALLOC_ENDED_[0-9]+"; do sleep 2; done' "$S"
tmux capture-pane -p -J -t "=$S:" -S -200 | grep -Ex '__READY_[0-9]+_.+|__ALLOC_ENDED_[0-9]+'

# 3. One command, its own numbered marker, one bounded wait, then the log.
tmux send-keys -t "=$S:" 'python check.py > probe-3.log 2>&1; echo "__END_3_$?"' Enter
timeout 900 bash -c 'until tmux capture-pane -p -J -t "=$0:" -S -200 |
  grep -Eqx "__END_3_[0-9]+|__ALLOC_ENDED_[0-9]+"; do sleep 10; done' "$S"
tmux capture-pane -p -J -t "=$S:" -S -200 | grep -Ex '__END_3_[0-9]+|__ALLOC_ENDED_[0-9]+'
tail -20 probe-3.log
```

- The session runs `bsub` with no login shell under it. Keys sent while the job pends, after a rejected
  submission, or after the job ends (watchdog release, walltime, `exit`) reach no shell on the login node.
- Send work only after the `__READY_` line shows a numeric job id and a host other than the login node. SLURM
  prints no `<<Starting on`: skip that wait, echo `$SLURM_JOB_ID` instead of `$LSB_JOBID`, and give the readiness
  wait the longer timeout.
- Number each marker anew, because the pane still holds the earlier ones. `grep -x` matches only a whole output
  line, never the typed line with its literal `$?`. On a nonzero code, read the log before sending anything else.
- Every wait is one bounded call that also stops on `__ALLOC_ENDED_`. After that, the allocation is gone: start a
  new session with a new name.
- `-t "=$S:"` targets that exact session. A bare `-t probe` also resolves to another session such as
  `probe-other`.
- The pane holds only recent lines (`-S -200` reads 200 back). Send long output to a log file in the run folder,
  which the login node can read. `tmux send-keys -t "=$S:" C-c` interrupts a command that hangs.
- A person can watch with `tmux attach -r -t <session>` (`-r` keeps it read-only, `Ctrl-b d` detaches).

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
- The agent steps above run as written in `tests/test_interactive_recipe.py`: local tmux 3.7, a private socket,
  and a fake `bsub -Is` that pends 2 s and then starts a shell with `LSB_JOBID` set, or is rejected with exit 255.
  Checked: a second `tmux new` with the same name fails, the readiness line names the job, the wait for
  `__END_4_` lasts for the 3 s command although `__END_3_3` is still on screen, and after a rejection the wait
  ends on `__ALLOC_ENDED_255` and keys typed ahead run nowhere. A longer manual run also checked that `=name:`
  resolves only the exact session (a bare `-t probe` resolved to `probe-other`) and that keys sent after `exit`
  run nowhere.
- Not yet run with real `bsub -Is` or `srun --pty`: the `<<Starting on` text, whether `bsub` passes on keys typed
  while pending, and the SLURM readiness step are unchecked.
- Not yet tested: `hpc-session-slurm`, several GPUs in one job, another tenant on the same GPU, and the
  scheduler's own pre-kill warning.
