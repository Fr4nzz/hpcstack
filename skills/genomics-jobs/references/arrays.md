# Job array patterns

## Manifest-driven array

```bash
# manifest.tsv (no header): sample  r1  r2
N=$(wc -l < manifest.tsv)
bsub -J "align[1-$N]%50" -n 4 -M 8000 -R "select[mem>=8000] rusage[mem=8000] span[hosts=1]" -W 3:00 \
     -o logs/%J.%I.out -e logs/%J.%I.err bash align.sh manifest.tsv out/
# SLURM: sbatch --array=1-$N%50 --cpus-per-task=4 --mem=8G --time=3:00:00 --output=logs/%A_%a.out align.sh ...
```

```bash
# align.sh
source /etc/profile 2>/dev/null || true
set -Eeuo pipefail
IDX=${LSB_JOBINDEX:-${SLURM_ARRAY_TASK_ID:?no array index}}
NCPU=${LSB_DJOB_NUMPROC:-${SLURM_CPUS_PER_TASK:-1}}
IFS=$'\t' read -r sample r1 r2 < <(sed -n "${IDX}p" "$1")
out=$2/$sample.bam
[[ -s $out && -s $out.bai ]] && { echo "done already: $sample"; exit 0; }
bwa mem -t "$NCPU" ref.fa "$r1" "$r2" \
  | samtools sort -@ $((NCPU - 1)) -m 1G -T "${TMPDIR:-/tmp}/$sample" -o "$out.tmp.bam" -
mv "$out.tmp.bam" "$out"
samtools index "$out"
```

## Rerun failed indices only

```bash
# LSF: indices that did not end DONE
bjobs -a -noheader -o 'jobindex stat' -J align | awk '$2 != "DONE" {print $1}' | paste -sd, -
bsub -J 'align[7,42,311]' ...          # same script, same manifest
# SLURM
sacct -n -X -j <arrayid> -o JobID,State | awk '$2 != "COMPLETED" {split($1, a, "_"); print a[2]}'
```

Or make the script skip finished units (the `[[ -s $out ]]` check above) and resubmit the whole range.

## Concurrency cap

`%N` limits how many tasks run at once. Pick it so that your array does not take a whole queue: tens, not
hundreds, unless the tasks are tiny and the queue is quiet. State the cap in the preflight receipt.

## Dependencies per unit

```bash
ORDER=$(bsub -J 'order[1-15]' ... bash order.sh | grep -o '[0-9]\+' | head -1)
bsub -J 'reorder[1-15]' -w "done($ORDER)" ... bash reorder.sh   # waits for every task of the array
# per-index (LSF): submit reorder task N with -w "done($ORDER[N])"
# SLURM: --dependency=aftercorr:<arrayid> runs task N after task N of the first array succeeds
```

## Merge at the end

```bash
bsub -J merge -w "done(<align_jobid>)" -n 4 -M 4000 ... samtools merge -@ 3 all.bam out/*.bam
```

## Many tiny tasks

If each unit takes under a minute, batch several units per task (task N handles lines
`(N-1)*k+1 .. N*k`). Scheduling overhead and log files otherwise dominate.
