---
name: cluster-ops
description: Submit, size and monitor jobs on a shared LSF or SLURM cluster, and clean up its storage. Use when the user says "run this on the cluster", "submit a job", "why is my job pending?", "check my jobs", "how much memory should I ask for?", "why was my job killed?", "give me a GPU shell", "we're over quota", "what can I delete?" or "run this nf-core pipeline", or pastes a `bsub` or `sbatch` command, and before you ssh to a login node, write a job script or open an interactive allocation.
---

# Cluster operations

Goal: the fewest wasted allocations, every request from a measurement, and every claim about a job backed by the
scheduler, the job's status file and its outputs.

## Know the site

Read this repo's `sites/<site>.md` if the cluster has one, and the user's `CLAUDE.md`/`AGENTS.md` for their group
and scratch path. Otherwise discover once (`lsid` or `sinfo -s`, `bqueues`, `lfs df -h`, `findmnt -T "$PWD"`) and ask
the user to confirm. Check queue limits (`bqueues -l <queue>`) rather than recall them, and whether `-M` counts per
job or per core (`LSB_JOB_MEMLIMIT`, `LSF_UNIT_FOR_LIMITS`). LSF: `references/lsf.md`. SLURM: `references/slurm.md`.

## Login-node etiquette

Everyone shares the login node. Run only light commands there: `bjobs`, `bqueues`, `lfs quota`, a bounded `stat`,
editing files, `bsub`. Scans (`du`, `find`, `ls -R`), compression, `pip install`, builds and anything over a minute
of CPU go in a job. Watchdogs run inside the allocation, never on the login node or from cron. Never
`tmux kill-server` there: it ends every session you have, including other agents' live ones. Close stale tmux
sessions one at a time, after checking that each is an idle shell.

## Interactive or batch

- Anything that runs longer than you are sure to be watching, or can be scripted, is a batch job or a worker-queue
  task (`cluster-autoresearch`). Batch jobs end on their own when the agent stops.
- Interactive is for probes, first-run debugging and commands that have no script yet. It always runs under
  `scripts/hpc-session` with a short walltime, driven by `scripts/hpc-probe`. An allocation without all three is a
  bug.
- Local GPU or cluster: for a GPU step over about 15 min, compare the recent PEND for the request plus cluster
  runtime and upload with the local GPU's measured time. Take the faster and say why.

```bash
P=/path/to/cluster-ops/scripts/hpc-probe   # login node, run folder. SLURM: srun --time=... --pty instead
$P start probe-<task> -- bsub -Is -q <queue> -n 2 -M 4000 -R "select[mem>=4000] rusage[mem=4000]" -W 2:00 \
   -wa USR2 -wt 10 /path/to/cluster-ops/scripts/hpc-session
$P run probe-<task> -- 'python check.py --quick'   # one command, at most 540 s, returns its exit code
$P stop probe-<task>
```

- `start` refuses a used name and returns once a job id and a compute host answer. No login shell sits under the
  session. `run` exits with the command's code, 124 on a timeout, 125 if the allocation is gone. Details:
  `references/interactive.md`.
- `-W 2:00` is the hard backstop. Many sites refuse to extend a running job, so choose it up front.
- `hpc-session` releases the job once nobody types and the job's CPU and GPUs are idle. A training inside it keeps
  the job alive.
- Ask for what the probe needs (1 GPU, a few cores, measured memory). CPU-only work goes to a CPU queue. Record
  the job id, probe name and purpose where the user can see them (`$P attach` prints a read-only watch command).
- `stop` exits the shell, confirms with `bjobs`/`squeue` that the job is gone (killing that exact id if needed),
  and only then closes the session. If the job stays listed, it keeps the session: keep monitoring and report.

## Size the request

Over-requesting is not safe padding: it lengthens the wait, burns fairshare and blocks co-tenants. Measure with
`scripts/jobeff <ids>` (`--lsf-log logs/*.out`, `--slurm <id>`, `--user`): efficiency, peak memory, flags and a
suggested next request. For GPUs, sample `nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 10`.
Raw fields, the ledger and the receipt: `references/sizing.md`.

- **Memory**: `-M` about 1.3 x the measured peak (cgroup RSS, not page cache), rounded up to the next 500 MB.
  With no measurement yet, say so and start modest. After a memory kill, find what grew instead of raising `-M` again.
- **Cores**: CPU efficiency is CPU time / (wall time x cores). Below 0.5, fix the tool's threads flag, then set
  `-n` to the cores it really uses. GPU jobs with data-loader workers get the measured busy cores rounded up, plus
  one (8.5 busy cores: `-n 10`, not 16).
- **Walltime**: about 1.5 x the observed p95 run time. Shorter walltimes backfill sooner.
- **GPU memory**: always set `gmem`, at about 1.3 x the measured GPU-memory peak (a modest floor such as 16G before
  one exists). Host RAM and VRAM are separate budgets: a CUDA OOM is not `TERM_MEMLIMIT`. A request above the
  smallest large-GPU class sharply raises the chance of waiting over an hour, so fit under it when the peak allows.
- **GPU busy**: a GPU under about 80 % busy is an input-pipeline problem (`optimize-pipeline`), not a reason for a
  second GPU. In a throughput array, each element's receipt records the GPU model and its measured rate.
- **Pilots don't scale for free.** Fit the peak against input size, or probe a full-size shard. Mixed inputs get
  one pilot per input class (its largest element). A peak from another code path is not a measurement.
- **Re-size every resubmit** at submit time: reused scripts keep their old numbers.
- **Count the hosts** that pass the GPU filter before several GPU runs. If your runs exceed them, order the runs
  or fit the next GPU class.
- Never cut `-M` or cores below what was measured just to start sooner. Find out why it pends (Recover).
- Append each verified run to the project's ledger, and read it before sizing a new job.
- PEND time cannot be predicted. `scripts/pendlog` logs waits and `scripts/pend-risk` gives the share of similar
  recent jobs that waited over 1 h (`references/sizing.md`, untested on a cluster).
- Genomics batch work (arrays per sample, Java heaps, linkage maps): `references/genomics.md`.

**Preflight.** Before anything goes to the scheduler, resolve every "no" (an unmeasured first run gets a modest
request, labelled as such). If you cannot resolve one, report it and wait for the user. Write the receipt to
`PREFLIGHT.md` in the run folder:

1. Cores, memory and walltime cite a measurement. `-W`, `-M` and `rusage` are set (queue memory defaults can be
   100 MB). Threads flag equals cores.
2. The shortest queue that fits. GPUs justified, GPU-hours stated. Shared GPU mode unless exclusive was measured
   to be needed and the user agreed.
3. Independent units are an array with a cap (`%N`) of tens, not hundreds (unless tasks are tiny and the queue
   is quiet), so it never takes a whole queue. No duplicate or speculative copies (check `bjobs` first).
4.  Quota checked, bytes and files (`scripts/quota_report.sh <mount> <group> --gate`). Expected new file count
   recorded. Pack above about 50 per run. Temp files and expanded archives on node-local disk. Output to a new
   versioned directory.
5. The job writes `status.json`, checkpoints, traps the walltime warning and cleans up. Dependencies use exact ids.
6. Every variant was dry-built on CPU in the job's own environment (imports, then data assembly).
7. You know what "finished correctly" looks like: files, counts, checksums.

## Submit

```bash
bsub -J name -q <queue> -n 4 -M 8000 -R "select[mem>=8000] rusage[mem=8000] span[hosts=1]" \
     -W 4:00 -o logs/%J.out -e logs/%J.err -wa USR2 -wt 10 bash job.sh
```

Arrays: `-J 'name[1-500]%50'` with `%I` in log names. Housekeeping gets `-sp 1`. `-wa USR2 -wt 10` warns ten
minutes before the limit so the job can save (`references/checkpointing.md`).

Job scripts start from `references/job-script-template.sh`:

- `source /etc/profile || true` and module setup first, then `set -Eeuo pipefail`. Call an absolute interpreter
  (`$ENV/bin/python`). Work under `${TMPDIR:-/tmp/$USER/$JOBID}` and prove it is local with `findmnt -T`.
- The EXIT trap copies the last ~20 kB of the log to the shared run folder, removes the work dir, stops helpers
  and never changes the exit code. Guard only optional diagnostics with `|| true`. Never let `|| true`, `tee` or a
  trailing `echo` hide the exit code of the real work.
- One bad input must not fail a whole shard: record per-item errors with the item id, and fail only above a
  stated rate. CPU-only stages go in CPU jobs, not inside a GPU allocation.
- Replace a script that a running job uses by writing a new file and `mv`. Lint with `bash -n` and `shellcheck`. A
  comment after a line-continuation backslash silently drops every later argument. Pass parameters as arguments or
  `-env`, and record them with the git commit in the output folder.

## Workflow managers

A pipeline of hand-written scripts or chained `bsub` jobs (reruns by hand, no resume, outputs overwritten) is worth
moving to Nextflow, or to an nf-core or sanger-tol pipeline that fits. With `-resume`, Nextflow reuses completed
tasks whose cache and outputs remain valid, and its trace shows what each step used. nf-core pipelines also retry a
killed task with more resources and accept site profiles. A Nextflow workflow you write yourself gets these only from
its own config. Recommend the move and let the user decide. Docs:
[Nextflow](https://www.nextflow.io/docs/latest/), [nf-core](https://nf-co.re/docs/),
[nf-core/configs](https://github.com/nf-core/configs) (site profiles).

- Run with the site profile (`-profile <site>`) and check what it sets:
  `nextflow config nf-core/<pipeline> -r <release> -profile <site>` (Nextflow 26.04 or later). On older versions,
  check out the release and run `nextflow config <checkout> -profile <site>`. Farm22: `sites/sanger-farm22.md`.
- The head job runs as a small, long batch job (2 cores, 8 GB, heap through `NXF_OPTS`), never on the login node.
- `work/` goes on scratch, never in a small home quota. Clean it after the results are published
  (`nextflow clean -f -k <run>`) unless the user keeps it for `-resume`. `cleanup = true` breaks `-resume`.
- Size processes from the trace and retry instead of padding (`references/sizing.md`). Batch items that take
  seconds, so each task does minutes of work.

## Wait and monitor

The job writes `status.json` (phase, progress, last metric, ETA). Wait with one background watcher that polls
every 60 s and exits on a terminal state, a long PEND or an error, never with repeated `sleep` calls. The loop,
long waits, stragglers and handoffs: `references/monitoring.md`.

`DONE` is a scheduler state, not a verdict. Before reporting a job finished, run the checks in
`references/monitoring.md`, "When it ends". Long jobs checkpoint and survive a kill (`references/checkpointing.md`).

With short-lived certificates (Teleport, Kerberos), read the real validity (`tsh status`, `klist`) and renew
before a long wait. Expiry blinds monitoring but does not kill jobs: re-authenticate, then re-check every job.

## Storage and cleanup

Bytes and files (inodes) are separate quotas, and on team scratch the file count is often the tighter one.
Deleting a 500 GB tar frees one inode. Packing 30,000 loose images into one tar frees 29,999. Prioritise by file
count when the inode quota is tight, by bytes when the byte quota is.

1. Quota first: `scripts/quota_report.sh <mount> [group]` reads `lfs quota`, walks nothing, and shows whether the
   user or the team is at the limit. Then ask which known trees (project dirs, run roots) are finished.
2. Bounded probes, one level at a time under `timeout` (`lfs find DIR --maxdepth 1 -type d`). `du`, `ls -lR` and
   parallel walkers stat every entry on the metadata server, and `du -d1` still walks the whole tree.
3. Anything bigger gets one walk in a low-priority 1-core job (`scripts/listing_job.sh DIR OUT.tsv --submit`).
   Answer every later question from that listing (`scripts/analyse_listing.py OUT.tsv --root DIR`).
4. Never walk the whole team tree or other users' directories, never scan from the login node, and run one scan
   of a filesystem at a time.

Cleanup is a plan table (path, bytes, files, why it is safe, action) that the user approves, every time. Pack
small-file trees with `tar --zstd`, and verify the entry count and a sha256 manifest before removing the
originals. Log every deletion, never touch other users' files, and report the quota before and after. Never
delete the source data of a trained or deployed model: pack it under a permanent folder with a README. Big wins,
local disks and prevention: `references/storage.md`.

A transfer can arrive silently truncated: compare `sha256sum` on both sides. Check an exact path with `stat` before
concluding a file is missing. Over ssh (`ssh host 'tar -c ...' > file`), stdout must carry only the stream.

## Recover

- **Why pending**: `bjobs -p -l <id>` (SLURM `squeue -j <id> -o %R`). Usual causes: a request bigger than free
  hosts have, a GPU-memory filter only the largest GPUs meet, a stale host selector, fairshare. Diagnose
  "Affinity ... not enough processor units" with `references/lsf.md` before cutting cores.
- **Kill** only the exact confirmed id, with the user's reason (releasing your own idle allocation is one). No
  pattern kills, no `bkill -r`, no kill lines in reusable scripts. Check the reply: with an expired login a kill
  can fail silently. Check a conditional approval ("stop it if progress is saved") before killing.
- Wait for `EXIT` before reusing output paths, or write to a new directory.
- When an upstream fails, its `done()` dependents pend forever: kill them by exact id in the same step.
- To swap your own running training for more valuable work, use a worker task and `queue_worker.py preempt`
  (`cluster-autoresearch`) instead of `bkill` plus a new job.
- After `TERM_MEMLIMIT` (exit 130 or 137) or a walltime kill (140), re-size from the measured peak or rate and
  resume from the last published checkpoint (`references/checkpointing.md`).

## Done means

- Scheduler state, `status.json`, the log tail and the output check agree.
- The next request cites measured numbers, and the ledger has a new row.
- Interactive allocations are released, as shown by the scheduler, and helper processes are gone.
- You list what you could not verify.
