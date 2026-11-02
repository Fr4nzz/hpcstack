---
name: storage-footprint
description: Audit and reduce disk and inode (file-count) usage on shared filesystems (Lustre, GPFS, NFS scratch) or local disks without overloading them. Use when the user asks "what can I delete?", "how much space am I using?", "we're over quota", "file limit reached", "disk full", "clean up scratch", "why is du so slow?", or before writing many files to shared storage.
---

# Storage footprint

Goal: a reviewed cleanup plan that frees the scarce resource (bytes or files), executed safely, with the
quota measured before and after. Scanning must not slow the filesystem down for everyone else.

## Know which resource is scarce

- **Bytes and inodes are separate quotas.** On Lustre, each file and directory is one inode on the metadata
  server (MDS). File data lives on object storage (OSTs).
- On team scratch the **file count** is often the tighter limit. Deleting a 500 GB tar frees one inode.
  Packing 30,000 loose images into one tar frees 29,999.
- Prioritise by file count when the inode quota is tight, by bytes when the byte quota is tight.

## Why recursive `du` is antisocial on Lustre

`du`, `ls -lR`, `find -size` call `stat()` on every entry. On Lustre each stat is an MDS request, plus a
size request to every OST holding a stripe. A million files means millions of requests on a server everyone
shares. `du -d1` and `--max-depth` limit only the output: the whole tree is still walked. Parallel walkers
(gdu, dust, pdu) are great on local NVMe and multiply the MDS load on Lustre. Detail:
`references/lustre-metadata.md`.

## Strategy, cheapest first

1. **Quota first.** `scripts/quota_report.sh <mount> [group]` (uses `lfs quota`, instant, walks nothing).
   It answers "is it me or the team?" and how close each limit is.
2. **List what you already know.** Project dirs, run roots, READMEs and reports that say what each run
   produced. Ask the user which trees are theirs and which are finished.
3. **Bounded probes.** One level at a time with a timeout, on a few named children:
   `timeout 120 lfs find DIR --maxdepth 1 -type d` then `timeout 300 lfs find DIR/sub -type f | wc -l`.
   `lfs find` without size tests asks only the MDS. Stop descending once a subtree is small.
4. **One walk in a low-priority job** for anything bigger: `scripts/listing_job.sh DIR OUT.tsv` prints (or
   with `--submit` submits) a 1-core, `-sp`/`--nice` job that writes one listing file. Then analyse it as
   often as you like: `scripts/analyse_listing.py OUT.tsv --root DIR`. Walk once, ask many questions.
5. Never walk the whole team tree or other users' directories. Never scan on the login node.

On a **local disk**: one `ncdu -x -o scan.json` or `gdu` scan, cached and reused. Don't run several `du`
processes on the same disk at once. Check known caches first (`~/.cache/pip`, `~/.cache/uv`, conda pkgs,
`~/.cache/huggingface`, `node_modules`, old containers).

## Typical big wins

- expanded datasets and staging trees that also exist as archives
- conda envs, venvs, `node_modules`, pip, uv and HF caches on scratch
- Nextflow `work/` directories of finished runs
- duplicate copies (same size, then same hash)
- split transfer parts whose whole file exists
- per-locus or per-tile outputs that could be one indexed file.

## Act safely

1. Write the plan as a table: path, bytes, files, why it is safe, confidence, action (delete, pack,
   archive elsewhere). The user approves it. Deletion needs that approval, every time.
2. Pack small-file trees with `tar --zstd -cf x.tar.zst DIR`, then verify (entry count from `tar -tf`
   equals the file count, plus a sha256 manifest of the originals) before removing the originals.
3. Never touch other users' files. Log every deletion (path, size, files, time).
4. Run `quota_report.sh` again and report before and after.

## Prevention

- Write the expected number of new files before submitting a job. Above about 50 per run, pack them.
- Expand archives on node-local disk inside jobs. Prove the path is local with `findmnt -T`.
- Gate submissions on quota: `quota_report.sh <mount> <group> --gate` exits non-zero above 95 % of a
  limit or below 10,000 files of headroom (both adjustable).

## Done means

- The quota report before and after, the approved plan, and the deletion log are in the run folder.
- Nothing outside the approved plan was touched. Packed trees were verified before removal.
