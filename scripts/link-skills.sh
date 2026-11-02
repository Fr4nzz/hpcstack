#!/usr/bin/env bash
# link-skills.sh: symlink this repo's skills into agent skill folders, so `git pull` updates them.
#
#   scripts/link-skills.sh [--claude] [--codex] [--agents] [--dry-run] [--remove]
#
# Targets: --claude ~/.claude/skills, --codex ~/.codex/skills, --agents ~/.agents/skills.
# No target flag = --claude. Existing non-symlink folders with the same name are never touched.
# --remove deletes only symlinks that point into this repo.
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGETS=() DRY=0 REMOVE=0
for a in "$@"; do
  case $a in
    --claude) TARGETS+=("$HOME/.claude/skills") ;;
    --codex) TARGETS+=("$HOME/.codex/skills") ;;
    --agents) TARGETS+=("$HOME/.agents/skills") ;;
    --dry-run) DRY=1 ;;
    --remove) REMOVE=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
(( ${#TARGETS[@]} )) || TARGETS=("$HOME/.claude/skills")

run() { if (( DRY )); then echo "would run: $*"; else "$@"; fi; }

for t in "${TARGETS[@]}"; do
  run mkdir -p "$t"
  for s in "$REPO"/skills/*/; do
    name=$(basename "$s"); dst=$t/$name; src=${s%/}
    if (( REMOVE )); then
      if [[ -L $dst && $(readlink "$dst") == "$src" ]]; then run rm "$dst"; echo "removed $dst"; fi
      continue
    fi
    if [[ -L $dst ]]; then
      [[ $(readlink "$dst") == "$src" ]] && { echo "ok      $dst"; continue; }
      run ln -sfn "$src" "$dst"; echo "relinked $dst -> $src"
    elif [[ -e $dst ]]; then
      echo "skip    $dst exists and is not a symlink (remove or rename it first)" >&2
    else
      run ln -s "$src" "$dst"; echo "linked  $dst -> $src"
    fi
  done
done
