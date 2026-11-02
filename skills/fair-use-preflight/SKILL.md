---
name: fair-use-preflight
description: A 60-second fairness and safety checklist before anything is submitted to a shared cluster, ending with a short receipt. Use when the user says "before I submit", "is this ok to launch?", "launch it", "submit it", "run it on the cluster", or right before you run bsub, sbatch, a job array, a Nextflow or Snakemake run, or open an interactive allocation.
---

# Fair-use preflight

Goal: nothing goes to the scheduler without a measured request, a time limit, a storage plan and a recovery
plan. It takes a minute. Skipping it is how a 1-core tool ends up holding 18 cores and 80 GB for a day.

Stop rule: if any item is "no" or "unknown", fix it or say so to the user before submitting. Don't block on
items that don't apply (mark them n/a).

## Checklist

**Resources**
- [ ] Cores, memory and walltime come from a measurement (ledger, `right-size-jobs`, a probe). If not,
      the receipt says "first run, unmeasured" and the request is modest.
- [ ] `-W`/`--time` is set.
- [ ] `-M` and `rusage`/`select` (or `--mem`) are set. The queue default is not a plan.
- [ ] The tool's threads flag equals the cores requested. Java has `-Xmx` at about 0.8 x `-M`.

**Queue and GPUs**
- [ ] The shortest queue that fits the walltime, chosen after looking at live queue state.
- [ ] Number of GPUs justified. GPU-hours stated ("4 runs x 6 h = 24 GPU-h").
- [ ] Shared GPU mode unless exclusive was measured to be needed and the user agreed.

**Arrays and volume**
- [ ] Independent units are an array with a concurrency cap (`%N`).
- [ ] No duplicate or speculative submissions. You checked `bjobs`/`squeue` for an identical job first.

**Storage**
- [ ] Quota checked, user and group, bytes and files (`storage-footprint/scripts/quota_report.sh --gate`).
- [ ] Expected new file count written down. Anything above about 50 per run is packed or merged.
- [ ] Expansion of archives and temp files happens on node-local disk, verified with `findmnt -T`.
- [ ] Output goes to a new versioned directory. Nothing running writes to the same paths.

**Interactive**
- [ ] Interactive only for probes, under `hpc-session` with a short walltime (`safe-interactive`).

**Recovery and proof**
- [ ] The job writes `status.json` and checkpoints, traps the walltime warning (`-wa USR2 -wt 10`), and
      cleans up on exit.
- [ ] Dependencies use `done()`/`afterok` on job ids. No `|| true` around the real work.
- [ ] You know what "finished correctly" will look like (files, counts, checksums).

## Receipt

Write it to the run folder as `PREFLIGHT.md` and show it to the user:

```text
Preflight <date> <job name>
Request: -q normal -n 4 -M 6000 -W 3:00, array [1-120]%30. Source: ledger row 2026-10-12 (peak 4.4 GB, 0.93 eff).
Cost estimate: 120 x 2.1 h x 4 cores = about 1,000 core-h. GPUs: none.
Storage: group files 72 % used, user bytes 1.1 TB of no limit. New files: 240 (2 per task), bam+bai.
Node-local temp: yes (findmnt: xfs). Output: out/v3/ (new).
Recovery: status.json every 5 min, USR2 trap, skip-if-done per sample. Deps: none.
Done check: 120 bam+bai, samtools quickcheck clean, flagstat per sample.
Open items: none.
```

Bad: "Submitted 120 jobs with -n 16 -M 64000 on the week queue to be safe."
Good: the receipt above, then submit.
