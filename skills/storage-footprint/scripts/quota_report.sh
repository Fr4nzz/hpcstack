#!/usr/bin/env bash
# quota_report.sh: user and group quota (bytes and files) on a Lustre mount, with an optional gate.
#
#   quota_report.sh MOUNT [GROUP ...] [--user NAME] [--gate] [--max-pct 95] [--min-files 10000]
#
# Uses `lfs quota`, which reads quota accounting and walks no files: safe on a login node.
# Default user: $USER. Default group: your primary group (id -gn).
# --gate exits 1 if any limit is above --max-pct percent used, or a file limit has fewer than
# --min-files files of headroom. Use it in submit scripts before writing many files.
# Not Lustre (or no lfs)? It falls back to df, which does NOT show user or group quotas.
# LFS=/path/to/lfs overrides the lfs binary (used by tests).
set -Eeuo pipefail

LFS=${LFS:-lfs}
MOUNT="" ; USER_NAME=${USER:-$(id -un)} ; GROUPS_=() ; GATE=0 ; MAX_PCT=95 ; MIN_FILES=10000
while (( $# )); do
  case $1 in
    --user) USER_NAME=$2; shift 2 ;;
    --gate) GATE=1; shift ;;
    --max-pct) MAX_PCT=$2; shift 2 ;;
    --min-files) MIN_FILES=$2; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [[ -z $MOUNT ]]; then MOUNT=$1; else GROUPS_+=("$1"); fi; shift ;;
  esac
done
[[ -n $MOUNT ]] || { sed -n '2,12p' "$0"; exit 2; }
(( ${#GROUPS_[@]} )) || GROUPS_=("$(id -gn)")

if ! command -v "$LFS" >/dev/null 2>&1 || ! "$LFS" quota -u "$USER_NAME" "$MOUNT" >/dev/null 2>&1; then
  echo "lfs quota not available for $MOUNT. df shows the whole filesystem, not your quota:"
  df -h "$MOUNT"; df -i "$MOUNT"
  exit 0
fi

# Print: kind name used_kb limit_kb files file_limit (limit = hard, else soft, else 0 for none)
parse() {
  awk -v kind="$1" -v name="$2" '
    /Filesystem/ { hdr = 1; next }
    hdr { for (i = 1; i <= NF; i++) { t = $i; if (t ~ /^\//) continue; gsub(/\*/, "", t); v[++n] = t } }
    END {
      if (n < 8) { print kind, name, "parse-error"; exit }
      bl = (v[3] + 0 > 0) ? v[3] : v[2]; fl = (v[7] + 0 > 0) ? v[7] : v[6]
      print kind, name, v[1] + 0, bl + 0, v[5] + 0, fl + 0
    }'
}

rows=$( { "$LFS" quota -u "$USER_NAME" "$MOUNT" | parse user "$USER_NAME"
          for g in "${GROUPS_[@]}"; do "$LFS" quota -g "$g" "$MOUNT" | parse group "$g"; done; } )

echo "Quota on $MOUNT ($(date -Is))"
fail=0
while read -r kind name used blim files flim; do
  if [[ $used == parse-error ]]; then echo "$kind $name: could not parse lfs output"; fail=1; continue; fi
  out=$(awk -v k="$kind" -v n="$name" -v u="$used" -v bl="$blim" -v f="$files" -v fl="$flim" \
            -v maxp="$MAX_PCT" -v minf="$MIN_FILES" '
    function h(kb) { s = "KMGTP"; i = 1; while (kb >= 1024 && i < 5) { kb /= 1024; i++ } return sprintf("%.1f%sB", kb, substr(s, i, 1)) }
    BEGIN {
      bp = (bl > 0) ? 100 * u / bl : -1; fp = (fl > 0) ? 100 * f / fl : -1
      line = sprintf("%-5s %-14s bytes %s / %s (%s)   files %d / %s (%s)", k, n, h(u),
        (bl > 0) ? h(bl) : "no limit", (bp >= 0) ? sprintf("%.1f%%", bp) : "-",
        f, (fl > 0) ? sprintf("%d", fl) : "no limit", (fp >= 0) ? sprintf("%.1f%%", fp) : "-")
      bad = ""
      if (bp > maxp) bad = bad " bytes>" maxp "%"
      if (fp > maxp) bad = bad " files>" maxp "%"
      if (fl > 0 && fl - f < minf) bad = bad " file-headroom<" minf
      print line (bad != "" ? "   GATE:" bad : "")
    }')
  echo "$out"
  [[ $out == *GATE:* ]] && fail=1
done <<< "$rows"

if (( GATE && fail )); then echo "quota gate FAILED (max ${MAX_PCT}%, min ${MIN_FILES} files headroom)"; exit 1; fi
exit 0
