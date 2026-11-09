# SLURM translation table

| Task | LSF | SLURM |
|---|---|---|
| batch submit | `bsub ... bash job.sh` | `sbatch job.sh` |
| interactive shell | `bsub -Is ... bash` | `srun --pty bash` or `salloc` then `srun --pty bash` |
| cores | `-n 8 -R "span[hosts=1]"` | `--cpus-per-task=8 --nodes=1` |
| memory | `-M 8000 -R "select[mem>=8000] rusage[mem=8000]"` (per job) | `--mem=8G` (per node) or `--mem-per-cpu` |
| walltime | `-W 4:00` | `--time=04:00:00` |
| queue | `-q normal` | `--partition=<partition>` (and sometimes `--qos`) |
| GPU | `-gpu "num=1"` | `--gres=gpu:1` or `--gpus=1` |
| array with cap | `-J 'x[1-500]%50'`, `$LSB_JOBINDEX` | `--array=1-500%50`, `$SLURM_ARRAY_TASK_ID` |
| logs | `-o logs/%J.%I.out` | `--output=logs/%A_%a.out` |
| dependency on success | `-w "done(123)"` | `--dependency=afterok:123` |
| warning signal | `-wa USR2 -wt 10` | `--signal=B:USR2@600` |
| low priority | `-sp 1` | `--nice=1000` |
| job id in the job | `$LSB_JOBID` | `$SLURM_JOB_ID` |
| cores granted | `$LSB_DJOB_NUMPROC` | `$SLURM_CPUS_PER_TASK` |
| list jobs | `bjobs -w` | `squeue -u $USER` |
| why pending | `bjobs -p -l <id>` | `squeue -j <id> -o %R`, `squeue --start` |
| usage of finished job | `bacct -l <id>`, `.out` summary | `sacct -j <id> --format=JobID,Elapsed,TotalCPU,AllocCPUS,MaxRSS,ReqMem,State`, `seff <id>` |
| usage of running job | `bjobs -o 'cpu_used run_time max_mem'` | `sstat -j <id> --format=AveCPU,MaxRSS` |
| extend walltime | `bmod -W` (if the site allows) | `scontrol update JobId=<id> TimeLimit=...` (users can usually only lower it) |
| kill | `bkill <id>` | `scancel <id>` |
| queue info | `bqueues -l <queue>` | `scontrol show partition <partition>`, `sinfo -p <partition>` |

Differences that bite:

- SLURM `--mem` is per node and `--mem-per-cpu` multiplies by cores. LSF `-M` is per job on most sites (check
  `LSB_JOB_MEMLIMIT` / `perJobMemLimit` in your site profile).
- SLURM users usually cannot raise `TimeLimit` on a running job. Pick a modest `--time` for interactive work
  and rely on the `scripts/hpc-session-slurm` watchdog.
- `seff` reports CPU and memory efficiency directly. `MaxRSS` is per step, so take the maximum over steps.
- SLURM's `afterany` is LSF's `ended()` and fires on failure too. LSF's `done()` is `afterok`.
