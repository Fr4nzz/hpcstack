# Site profiles

A site profile is one Markdown file of facts about one cluster: its scheduler, queues, limits, storage and
GPUs, so the generic skills can give advice that fits it. A user of that cluster writes it from the public docs
and read-only commands (start from `TEMPLATE.md`), and an agent reads it before working on that cluster
whenever the user's `CLAUDE.md` or `AGENTS.md` points to it.

## How skills use a profile

1. If the user's `CLAUDE.md`/`AGENTS.md` names a profile ("Cluster: see hpcstack sites/<site>.md"), the
   skill reads it first.
2. If there is none, the skill runs a short read-only discovery and asks the user to confirm:

   ```bash
   lsid || sinfo -s                 # scheduler and cluster name
   bqueues || sinfo                 # queues or partitions
   bqueues -l <queue> | grep -E 'RUNLIMIT|MEMLIMIT|HOSTS|IDLE' -A1
   lfs df -h 2>/dev/null; findmnt -T "$HOME"; findmnt -T "${TMPDIR:-/tmp}"
   module avail 2>&1 | grep -iE 'nextflow|singularity|apptainer|conda' | head
   ```
3. Profile facts are starting points. Anything that changes (limits, hosts, versions) is re-checked with
   the command the profile gives before a decision depends on it.

## What goes where

| Kind of fact | Where |
|---|---|
| Scheduler behaviour any user can verify (queue names, how memory is counted) | site profile |
| Public institutional config (nf-core profile, public docs) | site profile, with a link |
| Your group name, scratch path, preferred queues | your own `CLAUDE.md`/`AGENTS.md`, never this repo |
| Quota levels, other users' usage, internal wiki text, login endpoints | nowhere in this repo |

Copy `TEMPLATE.md` to start a new profile. See `CONTRIBUTING.md` for the rules.
