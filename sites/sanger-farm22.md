# Site profile: Wellcome Sanger Institute, farm22 (LSF)

Last checked: 2026-10-27. Built only from public sources and generic, read-only checks:

- nf-core/configs: [`conf/sanger.config`](https://github.com/nf-core/configs/blob/master/conf/sanger.config),
  [`conf/sanger/farm22.config`](https://github.com/nf-core/configs/blob/master/conf/sanger/farm22.config),
  [`docs/sanger.md`](https://github.com/nf-core/configs/blob/master/docs/sanger.md)
- sanger-tol pipelines' `conf/base.config` files on <https://github.com/sanger-tol>

Verify every limit on the cluster before relying on it.

## Scheduler

- IBM Spectrum LSF. The cluster name reported by `lsid` is `farm22` (the nf-core profile uses this to pick
  its farm22 settings).
- Memory is per job in the nf-core profile (`executor.perJobMemLimit = true`). Request it with `-M` together
  with `-R "select[mem>=X] rusage[mem=X]"`, the form the nf-core GPU settings use.
- Units: check `grep LSF_UNIT_FOR_LIMITS $LSF_ENVDIR/lsf.conf`. With `MB`, `-M 4000` is 4 GB.
- Default memory limits on queues can be very small. Check `bqueues -l <queue>` and always pass `-M`.
- Users cannot extend a running job: `bmod -W` answers "Cannot change job options for a running job" for
  both interactive and batch jobs, although `LSB_MOD_ALL_JOBS=Y` is set (tested October 2026). Pick `-W`
  up front and resubmit if it runs out.
- A running job's `-M` cannot be changed, and the site esub can refuse `bmod -R` or `bmod -mn` on pending
  GPU jobs. Resubmit instead.
- Users cannot ssh to compute nodes, so nobody can rescue a running job from outside. The job itself must
  publish anything worth saving.
- Idle-job exceptions: `bqueues -l <queue> | grep -A2 -i idle`. They notify only and are not defined on every
  queue, so they are no substitute for `hpc-session` (`cluster-ops`).

## Queues named in the public nf-core profile

| Queue | Used by the nf-core profile for |
|---|---|
| `normal` | tasks up to 12 h |
| `long` | tasks over 12 h, up to 2 days |
| `week` | tasks over 2 days, up to 7 days |
| `basement` | tasks over 7 days |
| `hugemem` | tasks above 350 GB of memory |
| `gpu` | GPU tasks up to 7 days |
| `gpu-basement` | GPU tasks over 7 days |
| `oversubscribed` | the Nextflow head job in the nf-core docs example |

Other queues exist, among them interactive and transfer queues (for an interactive session an agent can drive,
see `cluster-ops`, "Interactive or batch"). List them with `bqueues` and read limits with `bqueues -l <queue>`.
The nf-core profile caps tasks at 256 cores, 3.8 TB of memory and 30 days, which reflects the largest hosts and
queues. Check GPU host groups with `bqueues -l gpu | grep HOSTS` and `bhosts -gpu`.

## GPUs

- Start with `-gpu "num=1"`. The nf-core profile uses `-gpu 'num=1:j_exclusive=yes'` with
  `span[ptile=1]` for pipeline GPU tasks.
- Always set `gmem` (`cluster-ops`, "Size the request"). GPUs are shared between jobs here unless you ask for
  exclusive mode, so another job can already hold most of a GPU's memory: a plain `num=1` job ran out of memory
  at model load on a GPU where another process held 76 GB. Check the smallest GPU the queue reaches with
  `lshosts -gpu`.
- Ask for the measured busy cores plus one (`cluster-ops`, "Size the request"). When the few hosts that fit a
  large GPU request are short of cores, `-n` lengthens the wait. On average, GPU memory above 32G mattered more.
- "Affinity ... not enough processor units" here also appears when the `gmem` filter or a full queue is the
  real block. Diagnose it as in `cluster-ops/references/lsf.md` ("Why pending") before cutting cores.

## Nextflow

- Run the head job on a compute node, as `docs/sanger.md` shows: `-q oversubscribed -n 2 -M 8000` with
  `NXF_OPTS="-Xms8G -Xmx8G"`.
- Use `-profile sanger`. It sets the LSF executor, `perJobMemLimit = true`, `submitRateLimit = '5 sec'`,
  `killBatchSize = 50`, default tasks of 1 core and 6 GB, and queue selection by time and memory.
- Singularity binds `/lustre`, `/nfs`, `/data` and `/software` in the profile.
- Nextflow and Singularity come from environment modules rather than `PATH`. Find them with
  `module avail 2>&1 | grep -iE 'nextflow|singularity'` and pin the versions in your job script.
- For downloads from compute nodes, take the proxy settings from `docs/sanger.md`, not from a copied value.

## Storage

- Team scratch is Lustre. Use `lfs quota -u $USER <mount>` and `lfs quota -g <group> <mount>` before writing
  many files. The group file count is often the tighter limit.
- Node-local temp is under `$TMPDIR` inside jobs. Verify it is local with `findmnt -T "$TMPDIR"`.
- Audits: `cluster-ops`, "Storage and cleanup".

## Known-bad hosts

None listed. Keep your own dated list in your `CLAUDE.md`/`AGENTS.md`, with a re-check date, rather than
hard-coding host exclusions into job scripts.
