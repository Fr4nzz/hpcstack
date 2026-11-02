#!/usr/bin/env python3
"""results_tsv.py: keep an append-only results.tsv for an experiment loop.

Columns: id, metric, cost, status, description (tab-separated, one header line).
status is one of keep, discard, crash, baseline.

  results_tsv.py init  results.tsv
  results_tsv.py add   results.tsv --id w01_lr --metric 0.913 --cost 0.5 --status discard --desc "lr 2e-4"
  results_tsv.py best  results.tsv [--lower]       print the incumbent (best kept or baseline row)
  results_tsv.py check results.tsv                 validate format and unique ids
  results_tsv.py summary results.tsv [--lower]     counts per status, total cost, best so far

Crashes are recorded with an empty metric. Tabs and newlines in descriptions are replaced by spaces.
"""
import argparse
import csv
import os
import sys

COLUMNS = ["id", "metric", "cost", "status", "description"]
STATUSES = {"keep", "discard", "crash", "baseline"}


def read(path):
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    if not rows or rows[0] != COLUMNS:
        sys.exit(f"{path}: header must be {COLUMNS}")
    return [dict(zip(COLUMNS, r)) for r in rows[1:] if r]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def best(rows, lower):
    cands = [r for r in rows if r["status"] in ("keep", "baseline") and num(r["metric"]) is not None]
    if not cands:
        return None
    return (min if lower else max)(cands, key=lambda r: num(r["metric"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["init", "add", "best", "check", "summary"])
    p.add_argument("path")
    p.add_argument("--id")
    p.add_argument("--metric", default="")
    p.add_argument("--cost", default="")
    p.add_argument("--status", choices=sorted(STATUSES))
    p.add_argument("--desc", default="")
    p.add_argument("--lower", action="store_true", help="lower metric is better")
    a = p.parse_args(argv)

    if a.cmd == "init":
        if os.path.exists(a.path):
            sys.exit(f"{a.path} exists, not overwriting")
        with open(a.path, "w") as fh:
            fh.write("\t".join(COLUMNS) + "\n")
        return 0

    if a.cmd == "add":
        if not a.id or not a.status:
            sys.exit("add needs --id and --status")
        if a.status != "crash" and num(a.metric) is None:
            sys.exit("--metric must be a number unless --status crash")
        if a.cost and num(a.cost) is None:
            sys.exit("--cost must be a number")
        if not os.path.exists(a.path):
            main(["init", a.path])
        if any(r["id"] == a.id for r in read(a.path)):
            sys.exit(f"id {a.id} already in {a.path}. Results are append-only, use a new id")
        clean = " ".join(a.desc.split())
        with open(a.path, "a") as fh:
            fh.write("\t".join([a.id, a.metric, a.cost, a.status, clean]) + "\n")
        return 0

    rows = read(a.path)
    if a.cmd == "check":
        seen, bad = set(), 0
        for i, r in enumerate(rows, 2):
            problems = []
            if r["id"] in seen:
                problems.append("duplicate id")
            seen.add(r["id"])
            if r["status"] not in STATUSES:
                problems.append(f"bad status {r['status']!r}")
            if r["status"] != "crash" and num(r["metric"]) is None:
                problems.append("metric not a number")
            if r["cost"] and num(r["cost"]) is None:
                problems.append("cost not a number")
            if problems:
                bad += 1
                print(f"line {i} ({r['id']}): {', '.join(problems)}")
        print(f"{len(rows)} rows, {bad} with problems")
        return 1 if bad else 0

    b = best(rows, a.lower)
    if a.cmd == "best":
        if b is None:
            print("no kept or baseline row yet")
            return 1
        print("\t".join(b[c] for c in COLUMNS))
        return 0

    counts = {s: sum(r["status"] == s for r in rows) for s in sorted(STATUSES)}
    cost = sum(num(r["cost"]) or 0.0 for r in rows)
    print(f"rows {len(rows)}  " + "  ".join(f"{k} {v}" for k, v in counts.items()) + f"  total cost {cost:g}")
    print("best: " + ("none" if b is None else f"{b['id']} {b['metric']} ({b['description']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
