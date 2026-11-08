# Storage audits

## Why scans hurt on Lustre

A Lustre filesystem has one or a few metadata servers (MDS) and many object storage targets (OSTs). The MDS holds
one inode per file and directory. File contents are striped over OSTs. Bytes and file counts are separate quotas,
and `lfs quota` reads that accounting directly without walking anything.

| Command | Cost | Note |
|---|---|---|
| `lfs quota -u/-g` | tiny | always first |
| `ls` (no `-l`) | one readdir per directory | cheap on small dirs |
| `ls -l`, `stat`, `du` | one MDS request per entry, plus one size request per stripe | expensive on big trees |
| `lfs find DIR -type f` | MDS only | preferred for counting |
| `lfs find DIR -size +1G` | adds a size request per candidate | size only what you need |
| `du -d1` | full walk | the depth limits output only |
| gdu, dust, pdu | full walk, in parallel | fine on local NVMe, harsh on a shared MDS |

- Count with `lfs find DIR -type f | wc -l`. Probe one level at a time with `--maxdepth` under `timeout`.
- For a full picture, walk once in a low-priority 1-core job (`scripts/listing_job.sh`, `-sp`/`--nice`) and
  analyse the listing many times (`scripts/analyse_listing.py`: top directories by count and bytes, age,
  extensions, duplicates).
- Run one scan at a time, also across parallel agents or subagents. Several walks of one filesystem slow each
  other and everyone else.
- Prefer your centre's own Lustre guidance where it differs.

## Local disk

Check known caches first (`~/.cache/pip`, `~/.cache/uv`, conda pkgs, `~/.cache/huggingface`, container caches,
`node_modules`). Then one `ncdu -x -o scan.json` or `gdu` scan, cached and reused by every later question. On a
workstation most of the space freed usually came from caches and history, not from the scan.

## Typical big wins

- expanded datasets and staging trees that also exist as archives
- conda envs, venvs, pip, uv and Hugging Face caches on scratch
- Nextflow `work/` and Snakemake `.snakemake/` trees of finished runs
- duplicate copies (same size, then same hash) and split transfer parts whose whole file exists
- one output file per tile, locus or iteration that could be one indexed file

## Cleanup

1. Plan table: path, bytes, files, why it is safe, confidence, action (delete, pack, archive elsewhere). The user
   approves every deletion.
2. Pack small-file trees with `tar --zstd -cf x.tar.zst DIR`. Before removing the originals, check that the entry
   count from `tar -tf` equals the file count and keep a sha256 manifest of the originals.
3. Log every deletion (path, size, files, time). Never touch other users' files.
4. Never delete the source data of a trained or deployed model (the images, reads or tables it was built from).
   If its file count is the problem, pack it into plain tars with a manifest under a permanent folder with a
   README. One team deleted the 32,000 source photos behind a live model.
5. Run `scripts/quota_report.sh` again and report before and after.

## Prevention

- Every input a model or pipeline needs exists on the cluster's shared storage, not only on a workstation.
- Write the expected number of new files before submitting. Above about 50 per run, pack or merge them.
- Expand archives on node-local disk inside jobs, proved local with `findmnt -T`.
- Gate submissions on quota: `quota_report.sh <mount> <group> --gate` exits non-zero above 95 % of a limit or
  below 10,000 files of headroom (both adjustable).
