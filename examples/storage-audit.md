# Example: storage audits, the slow way and the cheap way

A case for `storage-footprint`. Three audits by agents, on a workstation and on a cluster.

## 1. Local disk: why `du` took seven minutes

A workstation disk was 85 % full (1.6 of 1.9 TB, btrfs). The agent's commands and their times:

| Command | Time | Why |
|---|---|---|
| `du -hd1 $HOME` | 427 s | `-d1` limits output, not traversal: the whole tree was walked |
| `du -hd2 ~/Documents` | 309 s | the same walk again, for a subtree |
| `du -xhd1 /` | 7 s | looked fast only because `-x` skipped the separate `/home` subvolume |
| `du` in a subagent on one project | killed after 253 s | several subagents walked the same disk at once |

The tree held millions of small files (images, virtual environments, git objects). Disk usage tools such as
`ncdu`, `gdu` and `dust` were not installed.

What freed space: about 173 GB, almost all from known caches and history, not from the scan. A package
manager cache (78 GB), an editor's snapshot store (46 GB), `git gc` on bloated repositories, old tarballs.

The cheap way: check known caches first (`~/.cache/*`, conda pkgs, container caches, `node_modules`), then
one `ncdu -x -o scan.json` (or `gdu`) scan, cached and reused by every later question. One walker at a time.

## 2. Cluster scratch: file count, not bytes

A team's Lustre scratch had hit its 50 million file limit. One user accounted for 0.4 % of the files, but
their own jobs still failed when they tried to write.

Method:
1. `lfs quota -u` and `lfs quota -g`: instant, and showed the group, not the user, was at the limit.
2. Listings with `find -printf` **inside 1-core batch jobs**, not on the login node.
3. A plan table, approved by the user: 318 entries deleted (165.6 GB), and 7,202 small files packed into
   tar archives and verified before removal, which freed about 7,400 inodes.

The bytes freed were large, but for the quota that mattered the packing did more: deleting a 100 GB file
frees one inode.

## 3. One walk, many questions

A later audit of a user's project area ran one low-priority job: 1 core, `-sp 1`, a 4-hour limit, `nice`
and `ionice`. It walked about 191,000 entries in 31 minutes and wrote one 36 MB listing. Every question
after that (top directories by count and by bytes, age, extensions, duplicates) was answered from the
listing file in seconds, without touching the metadata server again. That run deleted 382.6 GB under an
approved plan.

`scripts/listing_job.sh` and `scripts/analyse_listing.py` in `storage-footprint` implement this pattern.

## Lessons

- `lfs quota` first. It costs nothing and tells you whose problem it is.
- Depth flags on `du` do not limit the walk.
- Several parallel scans of the same filesystem slow each other and everyone else.
- On team scratch, prioritise by file count. Pack small-file trees, verify, then delete.
- Walk once in a job, analyse the listing many times.
