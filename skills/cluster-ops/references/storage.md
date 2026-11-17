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
- Run one scan at a time, counting parallel agents and subagents. Prefer your centre's own Lustre guidance.

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

- The cleanup plan table (SKILL.md, Storage and cleanup) also gets a confidence column. Before removing a packed
  tree, check that the entry count from `tar -tf` equals the file count.
- Every input a model or pipeline needs exists on the cluster's shared storage, not only on a workstation.
- `quota_report.sh <mount> <group> --gate` exits non-zero above 95 % of a limit or below 10,000 files of headroom
  (both adjustable), so a submit script can stop on it.

## Transfers

On a long path (another continent, 150 ms or more) one TCP stream is slow and unsteady: every packet loss cuts
its rate, and the sender, often a bastion or proxy, is not yours to tune. Several streams at once add up. Each
new ssh session also has a fixed setup cost (seconds through a proxy), so many small files sent one by one are
slow whatever the bandwidth.

`scripts/xfer` runs on your machine. Set `XFER_SSH` to your ssh command (`ssh`, or `tsh ssh` for Teleport).

```bash
xfer get -n 8 <host>:/abs/path/model.pt model.pt      # one big file in 8 byte ranges
xfer many -P 8 <host> paths.txt out/                  # many medium files, one stream each
xfer put results.tar <host>:/abs/path/results.tar     # one verified upload
<ssh> <host> 'tar -C /abs/dir -cf - sub' > sub.tar    # hundreds of tiny files: one stream, then tar -tf
```

- `get` checks every range against a hash made on the cluster from the same bytes, then the whole file against
  `sha256sum`. Re-running resumes from the verified ranges. `many` skips files already done.
- `put` writes to a temporary name, compares hashes and only then renames. It never overwrites.
- Measured on one 175 ms path through a Teleport proxy (November 2026): one stream gave 0.1 to 11 MB/s, usually
  1.6 to 3.5, and changed within minutes. 8 streams gave 11.7 MB/s, and 16 were slower than 8. Uploads ran 6
  to 7 MB/s on one stream. Start at 8 and measure on your own path.
- If plain OpenSSH works, a `ControlMaster` connection cuts the setup of each later command roughly in half.
- Never run a VPN, tunnel or daemon on a cluster login node to speed up transfers. Ask the site about a data
  transfer node instead.

