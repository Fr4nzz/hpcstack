#!/usr/bin/env bash
# Job script template for LSF or SLURM. Copy it, then edit the WORK section.
#
# Submit (LSF):   bsub -J myjob -q <queue> -n 4 -M 8000 -R "select[mem>=8000] rusage[mem=8000] span[hosts=1]" \
#                      -W 4:00 -o logs/%J.out -e logs/%J.err -wa USR2 -wt 10 bash job.sh <out_dir>
# Submit (SLURM): sbatch --cpus-per-task=4 --mem=8G --time=04:00:00 --signal=B:USR2@600 \
#                      --output=logs/%j.out job.sh <out_dir>
#
# Replace this file on the cluster by writing a new copy and `mv` over it. Never edit it while jobs run it.

# 1. Environment first. Some site profiles return non-zero, which would abort a script under set -e.
source /etc/profile 2>/dev/null || true
# module load <tool>/<version> || true     # pin versions
PY=${PY:-python3}                          # prefer an absolute interpreter, e.g. $ENV/bin/python

# 2. Then fail closed.
set -Eeuo pipefail

OUT_DIR=${1:?usage: job.sh <out_dir>}
JOBID=${LSB_JOBID:-${SLURM_JOB_ID:-manual$$}}
NCPU=${LSB_DJOB_NUMPROC:-${SLURM_CPUS_PER_TASK:-1}}
export OMP_NUM_THREADS=$NCPU OPENBLAS_NUM_THREADS=$NCPU MKL_NUM_THREADS=$NCPU
# Data-loader trainings: set these to 1 inside each worker instead (see optimize-pipeline).

# 3. Node-local work dir, proven local.
WORK=${TMPDIR:-/tmp/$USER}/job_$JOBID
mkdir -p "$WORK" "$OUT_DIR"
fstype=$(findmnt -n -o FSTYPE -T "$WORK" || echo unknown)
case $fstype in
  lustre|gpfs|nfs*|beegfs|cifs) echo "refusing: $WORK is on shared storage ($fstype)" >&2; exit 2 ;;
esac

status() {  # status.json is overwritten in place: one file, fixed inode count
  printf '{"job":"%s","phase":"%s","detail":"%s","time":"%s"}\n' \
    "$JOBID" "$1" "${2:-}" "$(date -Is)" > "$OUT_DIR/status.json.tmp"
  mv -f "$OUT_DIR/status.json.tmp" "$OUT_DIR/status.json"
}

HELPERS=()
cleanup() {  # must never change the exit code: every step `|| true`, then exit with the work's code
  local rc=$?
  for p in "${HELPERS[@]:-}"; do [[ -n $p ]] && kill "$p" 2>/dev/null || true; done
  rm -rf "$WORK" || true
  if (( rc == 0 )); then status "done" || true; else status failed "exit $rc" || true; fi
  exit "$rc"
}
trap cleanup EXIT
# Background helpers: a subshell with errexit and the ERR trap off, so a failed copy cannot kill it
# silently. Record the pid. Check from outside that the published log keeps advancing.
# ( set +eE; trap - ERR; while sleep 300; do cp -f "$WORK/log" "$OUT_DIR/log.tmp" && mv -f "$OUT_DIR/log.tmp" "$OUT_DIR/log" || echo "log copy failed" >&2; done ) &
# HELPERS+=($!)
trap 'status warned "walltime warning"; exit 140' USR2   # publish what you can here

status running "start on $(hostname) with $NCPU cores"

# ---- WORK ---------------------------------------------------------------
# Example: copy inputs in, compute, write the result to a temp name, publish by atomic mv.
# cp /path/to/input "$WORK/"
# "$PY" my_tool.py --threads "$NCPU" --in "$WORK/input" --out "$WORK/result.tmp"
echo "replace this with the real work" > "$WORK/result.tmp"
# -------------------------------------------------------------------------

cp -f "$WORK/result.tmp" "$OUT_DIR/result.txt.tmp"       # cross-filesystem copy to a temp name
mv -f "$OUT_DIR/result.txt.tmp" "$OUT_DIR/result.txt"     # then an atomic rename on the target
(cd "$OUT_DIR" && sha256sum result.txt > result.txt.sha256)
