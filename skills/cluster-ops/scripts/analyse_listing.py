#!/usr/bin/env python3
"""analyse_listing.py: answer storage questions from ONE listing file instead of re-walking the disk.

  analyse_listing.py LISTING.tsv[.gz] [--root DIR] [--depth 2] [--top 20] [--min-dup-mb 10]

Input lines (from listing_job.sh): type, size_bytes, mtime_epoch, path, tab-separated. Lines with three
fields (size, mtime, path) are also accepted and counted as files.

Prints markdown sections:
  - totals (entries, files, dirs, bytes)
  - top directories at --depth below --root, by entries (inodes) and by bytes
  - age histogram by last modification (entries and bytes)
  - top extensions by entries and by bytes
  - known space hogs (caches, envs, node_modules, Nextflow work dirs, ...)
  - duplicate candidates: files with the same size and the same name (confirm with sha256 before acting)
Nothing on disk is read except the listing.
"""
import argparse
import gzip
import os
import re
import sys
import time
from collections import defaultdict

HOG_PATTERNS = [
    ("conda/venv env", re.compile(r"/(envs|\.?venv|conda|miniconda3|mambaforge|micromamba)/")),
    ("package cache", re.compile(r"/(\.cache/(pip|uv|huggingface|torch)|pkgs|\.npm|\.conda/pkgs)/")),
    ("node_modules", re.compile(r"/node_modules/")),
    ("nextflow work", re.compile(r"/work/[0-9a-f]{2}/[0-9a-f]{30}")),
    ("snakemake state", re.compile(r"/\.snakemake/")),
    ("python bytecode", re.compile(r"/__pycache__/")),
    ("git objects", re.compile(r"/\.git/objects/")),
    ("split transfer part", re.compile(r"\.(part\d*|partial|crdownload)$|\.\d{3}$")),
]
TYPES = set("fdlspbcD")
AGE_BUCKETS = [(30, "< 30 d"), (90, "30-90 d"), (365, "90 d - 1 y"), (730, "1-2 y"), (float("inf"), "> 2 y")]


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def parse_line(line):
    line = line.rstrip("\n")
    parts = line.split("\t", 3)
    if len(parts) == 4 and parts[0] in TYPES and parts[1].isdigit():
        typ, size, mtime, path = parts
    else:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            return None
        typ, (size, mtime, path) = "f", parts
    num = re.match(r"\d+(\.\d+)?", mtime)
    if not size.isdigit():
        return None
    return typ, int(size), float(num.group(0)) if num else None, path


def table(title, header, rows):
    print(f"\n## {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("listing")
    p.add_argument("--root", help="directory the listing was taken from (default: common prefix)")
    p.add_argument("--depth", type=int, default=2, help="directory depth below root to aggregate at")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-dup-mb", type=float, default=10.0, help="ignore smaller files for duplicates")
    p.add_argument("--now", type=float, default=None, help="reference time (epoch) for ages, default now")
    a = p.parse_args(argv)

    opener = gzip.open if a.listing.endswith(".gz") else open
    entries = []
    bad = 0
    with opener(a.listing, "rt", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = parse_line(line)
            if r is None:
                bad += 1
            else:
                entries.append(r)
    if not entries:
        sys.exit("no parsable lines")
    root = (a.root or os.path.commonpath([e[3] for e in entries])).rstrip("/") or "/"
    now = a.now or time.time()

    n_files = sum(e[0] == "f" for e in entries)
    n_dirs = sum(e[0] == "d" for e in entries)
    total = sum(e[1] for e in entries if e[0] == "f")
    print(f"# Listing analysis: {root}\n")
    print(f"- entries (inodes): {len(entries):,} ({n_files:,} files, {n_dirs:,} directories, "
          f"{len(entries) - n_files - n_dirs:,} other)")
    print(f"- file bytes: {human(total)}")
    if bad:
        print(f"- unparsable lines skipped: {bad}")

    by_dir_n, by_dir_b = defaultdict(int), defaultdict(int)
    ages_n, ages_b = defaultdict(int), defaultdict(int)
    ext_n, ext_b = defaultdict(int), defaultdict(int)
    hog_n, hog_b = defaultdict(int), defaultdict(int)
    by_size = defaultdict(list)
    for typ, size, mtime, path in entries:
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        parent = parts if typ == "d" else parts[:-1]
        key = os.sep.join(parent[: a.depth]) or "."
        by_dir_n[key] += 1
        by_dir_b[key] += size if typ == "f" else 0
        if mtime is not None:
            days = (now - mtime) / 86400
            label = next(lbl for lim, lbl in AGE_BUCKETS if days < lim)
            ages_n[label] += 1
            ages_b[label] += size if typ == "f" else 0
        if typ != "f":
            continue
        base = os.path.basename(path)
        ext = base.rsplit(".", 1)[-1].lower() if "." in base.strip(".") else "(none)"
        if ext in ("gz", "zst", "bz2", "xz") and base.count(".") >= 2:
            ext = ".".join(base.lower().rsplit(".", 2)[-2:])
        ext_n[ext] += 1
        ext_b[ext] += size
        for name, rx in HOG_PATTERNS:
            if rx.search(path):
                hog_n[name] += 1
                hog_b[name] += size
                break
        if size >= a.min_dup_mb * 1024 * 1024:
            by_size[(size, base)].append(path)

    top = a.top
    table(f"Top {top} directories by entries (depth {a.depth})", ["dir", "entries", "bytes"],
          [(d, f"{by_dir_n[d]:,}", human(by_dir_b[d])) for d in sorted(by_dir_n, key=by_dir_n.get, reverse=True)[:top]])
    table(f"Top {top} directories by bytes (depth {a.depth})", ["dir", "bytes", "entries"],
          [(d, human(by_dir_b[d]), f"{by_dir_n[d]:,}") for d in sorted(by_dir_b, key=by_dir_b.get, reverse=True)[:top]])
    table("Age (last modified)", ["age", "entries", "bytes"],
          [(lbl, f"{ages_n[lbl]:,}", human(ages_b[lbl])) for _, lbl in AGE_BUCKETS if ages_n[lbl]])
    table(f"Top {top} extensions by entries", ["ext", "files", "bytes"],
          [(e, f"{ext_n[e]:,}", human(ext_b[e])) for e in sorted(ext_n, key=ext_n.get, reverse=True)[:top]])
    table(f"Top {top} extensions by bytes", ["ext", "bytes", "files"],
          [(e, human(ext_b[e]), f"{ext_n[e]:,}") for e in sorted(ext_b, key=ext_b.get, reverse=True)[:top]])
    if hog_n:
        table("Known space hogs", ["kind", "files", "bytes"],
              [(k, f"{hog_n[k]:,}", human(hog_b[k])) for k in sorted(hog_n, key=hog_n.get, reverse=True)])
    dups = [(s, b, ps) for (s, b), ps in by_size.items() if len(ps) > 1]
    dups.sort(key=lambda x: x[0] * (len(x[2]) - 1), reverse=True)
    if dups:
        table(f"Duplicate candidates (same size and name, >= {a.min_dup_mb:g} MB). Confirm with sha256",
              ["name", "size", "copies", "reclaimable", "paths"],
              [(b, human(s), len(ps), human(s * (len(ps) - 1)), "<br>".join(sorted(ps)[:4])
                + (" ..." if len(ps) > 4 else "")) for s, b, ps in dups[:top]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
