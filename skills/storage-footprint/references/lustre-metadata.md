# Lustre metadata and why scans hurt

## The architecture in one paragraph

A Lustre filesystem has one or a few metadata targets (MDTs, served by MDS nodes) and many object storage
targets (OSTs). The MDT holds the namespace: one inode per file and directory, with names, permissions and
layout. File contents are striped over one or more OSTs. Quotas are tracked separately: bytes are summed over
OSTs and files are counted on the MDT. `lfs quota` reads that accounting directly and walks nothing.

## Cost of common commands

| Command | MDS work | OST work | Note |
|---|---|---|---|
| `lfs quota -u/-g` | tiny | tiny | always do this first |
| `ls` (no `-l`) | one readdir per directory | none | cheap on small dirs |
| `ls -l`, `stat`, `du` | one getattr per entry | one size request per stripe (glimpse) | expensive on big trees |
| `find DIR -name x` | readdir + often a stat per entry | none for name-only tests on many systems | |
| `lfs find DIR -type f` | readdir and MDS attributes | none | preferred for counting |
| `lfs find DIR -size +1G` | as above | glimpse for each candidate | size tests touch OSTs |
| `lfs find --lazy` | uses Lazy Size-on-MDT | none | size may be stale |
| `du -d1` | full walk | full | depth limits output only |
| gdu / dust / pdu | full walk, in parallel | full, in parallel | fine locally, harsh on a shared MDS |

## Rules of thumb

- Count with `lfs find DIR -type f | wc -l`. Size only what you need: `lfs find DIR -size +1G -mtime +90`.
- Use `--maxdepth` to probe one level at a time, under `timeout`.
- For a full picture, walk once in a low-priority 1-core batch job and write one listing file. Analyse the
  file, not the filesystem.
- Avoid running several scans at once, including from parallel agents or subagents.
- Many centres publish similar guidance (search your centre's docs for "Lustre best practices"). Prefer
  their rules when they differ.

## Small-file anti-patterns that create the problem

- one output file per sample-locus, per tile, or per iteration
- expanding image datasets or environments on scratch instead of node-local disk
- leaving Nextflow `work/` or Snakemake `.snakemake/` trees after a run
- writing logs as one file per task for arrays of tens of thousands of tasks.
