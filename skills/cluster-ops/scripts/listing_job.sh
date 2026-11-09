#!/usr/bin/env bash
# listing_job.sh: walk a directory tree ONCE, in a low-priority 1-core batch job, into one listing file.
#
#   listing_job.sh DIR OUT.tsv [--submit] [--queue Q] [--hours 4] [--run-here]
#
# Default: print the bsub command and exit (review it first). --submit submits it.
# --run-here runs the walk in the current shell (only inside a job or on a local disk, never on a login node).
#
# Output: one line per entry, tab-separated: type size_bytes mtime_epoch path
#   (type f = file, d = directory, l = symlink). Analyse it with analyse_listing.py.
# Uses `lfs find --printf` when the target is Lustre and lfs supports the format, else GNU find.
# Runs under nice and ionice. Writes OUT.tsv.tmp and renames it on success.
set -Eeuo pipefail

usage() { sed -n '2,13p' "$0"; exit "${1:-0}"; }
DIR="" OUT="" SUBMIT=0 QUEUE="" HOURS=4 HERE=0
while (( $# )); do
  case $1 in
    --submit) SUBMIT=1; shift ;;
    --queue) QUEUE=$2; shift 2 ;;
    --hours) HOURS=$2; shift 2 ;;
    --run-here) HERE=1; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option $1" >&2; usage 2 ;;
    *) if [[ -z $DIR ]]; then DIR=$1; elif [[ -z $OUT ]]; then OUT=$1; else usage 2; fi; shift ;;
  esac
done
[[ -n $DIR && -n $OUT ]] || usage 2
DIR=$(readlink -f "$DIR"); OUT=$(readlink -f "$OUT")
[[ -d $DIR ]] || { echo "not a directory: $DIR" >&2; exit 2; }

walk() {
  local tmp=$OUT.tmp fs
  fs=$(findmnt -n -o FSTYPE -T "$DIR" 2>/dev/null || echo unknown)
  local fmt='%y\t%s\t%T@\t%p\n' probe
  if [[ $fs == lustre ]] && command -v lfs >/dev/null 2>&1; then
    # check that this lfs understands the format: field 3 must be a number
    probe=$(lfs find "$DIR" --maxdepth 0 --printf "$fmt" 2>/dev/null | head -1 || true)
    if [[ $(printf '%s' "$probe" | cut -f3) =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
      echo "walking $DIR with lfs find" >&2
      nice -n 19 ionice -c3 lfs find "$DIR" --printf "$fmt" > "$tmp"
      mv -f "$tmp" "$OUT"; return
    fi
    echo "lfs find --printf not usable here, using GNU find" >&2
  fi
  echo "walking $DIR with find" >&2
  nice -n 19 ionice -c3 find "$DIR" -printf "$fmt" > "$tmp"
  mv -f "$tmp" "$OUT"
}

if (( HERE )); then
  start=$(date +%s); walk
  echo "wrote $OUT: $(wc -l < "$OUT") entries in $(( $(date +%s) - start )) s" >&2
  exit 0
fi

command -v bsub >/dev/null 2>&1 || { echo "no bsub found. Use --run-here on a local disk." >&2; exit 2; }
SELF=$(readlink -f "$0")
LOG=${OUT%.tsv}.log
INNER=$(printf '%q ' bash "$SELF" "$DIR" "$OUT" --run-here)
cmd=(bsub -J listing -n 1 -M 1000 -R "select[mem>=1000] rusage[mem=1000]" -W "$HOURS:00" -sp 1 -o "$LOG"
     ${QUEUE:+-q "$QUEUE"} "$INNER")

if (( SUBMIT )); then
  "${cmd[@]}"
else
  echo "Review, then rerun with --submit (or paste):"
  printf '%q ' "${cmd[@]}"; echo
fi
