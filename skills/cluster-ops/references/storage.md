# Storage audits

## Why scans hurt on Lustre

A Lustre filesystem has one or a few metadata servers (MDS) and many object storage targets (OSTs). The MDS holds
one inode per file and directory. File contents are striped over OSTs. Bytes and file counts are separate quotas,
and `lfs quota` reads that accounting directly without walking anything.

| Command | Cost | Note |
|---|---|---|
| `lfs quota -u/-g` | tiny | always first |
| `ls -l`, `stat`, `du` | one MDS request per entry, plus one size request per stripe | expensive on big trees |
| `lfs find DIR -type f` | MDS only | preferred for counting |
| `lfs find DIR -size +1G` | adds a size request per candidate | size only what you need |
| `du -d1` | full walk | the depth limits output only |
| gdu, dust, pdu | full walk, in parallel | fine on local NVMe, harsh on a shared MDS |

- Count files with `lfs find DIR -type f | wc -l`.
- `scripts/analyse_listing.py` answers top directories by count and bytes, age, extensions and duplicates from
  one listing.
- Run one scan at a time, also across parallel agents or subagents. Prefer your centre's own Lustre guidance.

## Local disk

Check known caches first (`~/.cache/pip`, `~/.cache/uv`, conda pkgs, `~/.cache/huggingface`, container caches,
`node_modules`). Then one `ncdu -x -o scan.json` or `gdu` scan, cached and reused by every later question.

## Typical big wins

- expanded datasets and staging trees that also exist as archives
- conda envs, venvs, pip, uv and Hugging Face caches on scratch
- Nextflow `work/` and Snakemake `.snakemake/` trees of finished runs
- duplicate copies (same size, then same hash) and split transfer parts whose whole file exists
- one output file per tile, locus or iteration that could be one indexed file

## Cleanup and prevention

- The cleanup plan table (SKILL.md, Storage and cleanup) also gets a confidence column. Before removing a packed tree, check that the entry count from
  `tar -tf` equals the file count.
- Every input a model or pipeline needs exists on the cluster's shared storage, not only on a workstation.
- `quota_report.sh <mount> <group> --gate` exits non-zero above 95 % of a limit or below 10,000 files of headroom
  (both adjustable), so a submit script can stop on it.
