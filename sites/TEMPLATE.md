# Site profile: `<institution>` `<cluster>`

> A site profile holds the facts about one cluster (scheduler, queues, limits, storage, GPUs), so the generic
> skills give advice that fits it. A user of the cluster writes it from public docs and read-only commands, and
> an agent reads it when the user's `CLAUDE.md` or `AGENTS.md` points to it. Copy this file to
> `sites/<site>-<cluster>.md` and delete this note. Give the source of each fact, date anything that changes, and
> leave out internal wiki text, quota levels, other users' usage and login endpoints.

Last checked: `<YYYY-MM-DD>`. Sources: `<links to public docs>`, plus read-only commands.
Re-verify anything dated more than 30 days ago before relying on it.

## Scheduler

- Scheduler and version family: `<LSF 10 / SLURM 23.x>` (check: `lsid` / `sinfo -V`)
- Memory request semantics: `<per job / per core / per node>` (LSF: `LSB_JOB_MEMLIMIT`, `LSF_UNIT_FOR_LIMITS`)
- Can users extend the walltime of a running job? `<yes, bmod -W, up to the queue limit / no>`
- Default memory when none is requested: `<value>` (check: `bqueues -l <queue>`). Always pass memory anyway.
- Job exceptions or idle detection: `<which queues, notify only?>`

## Queues or partitions

| Queue | Use for | Run limit (check with) | Notes |
|---|---|---|---|
| `<normal>` | default CPU work | `bqueues -l normal` | |
| `<long>` | | | |
| `<gpu>` | GPU jobs | | GPU syntax below |
| `<interactive>` | interactive CPU | | |

## GPUs

- Request syntax: `<-gpu "num=1" / --gres=gpu:1>`
- Shared or exclusive mode: `<default, how to request>`
- How to list GPU hosts: `<bhosts -gpu / sinfo -o "%N %G">`

## Storage

| Mount | Kind | Purpose | Quota command | Notes |
|---|---|---|---|---|
| `</scratch>` | Lustre | team scratch | `lfs quota -u $USER /scratch`, `-g <group>` | file count often the tighter limit |
| `<$HOME>` | NFS | config, small files | `quota -s` | small |
| `<node-local>` | local disk | job temp | n/a | path: `$TMPDIR` (verify with `findmnt -T`) |

## Software

- Modules: `<module avail <name>>`. Pin versions in job scripts.
- Containers: `<singularity / apptainer module>`, bind paths: `<...>`
- Nextflow: `<module or install method>`, institutional profile: `<-profile <site>, link>`

## Network

- Outbound internet from compute nodes: `<yes / via proxy (link to the official doc) / no>`

## Known-bad hosts (dated, expire after 30 days)

| Host | Seen | Symptom | Re-verify with |
|---|---|---|---|
| `<bad-host>` | `<date>` | `<symptom>` | `<bhosts -l <host> or a 1-minute test job>` |

## Access

- Login method: `<ssh / Teleport / OIDC>`. Certificate lifetime: `<hours>`. Expiry does not kill jobs.
