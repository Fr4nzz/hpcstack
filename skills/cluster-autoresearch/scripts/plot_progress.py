#!/usr/bin/env python3
"""plot_progress.py: Karpathy-style progress plot for an experiment loop.

Every experiment is a grey dot, kept experiments are green dots with a short label, and a green step line
shows the running best over kept (and baseline) rows. Crashes are marked with a small x at the bottom edge.

  plot_progress.py results.tsv -o progress.png [--lower] [--title T] [--ylabel Y] [--no-labels]

Input is the results.tsv written by results_tsv.py (id, metric, cost, status, description). Rows are
plotted in file order, so the x axis is the experiment index. Needs matplotlib.
"""
import argparse
import csv
import sys

GREY, GREEN, INK, MUTED = "#a3a3a3", "#1a9850", "#262626", "#737373"


def load(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    for r in rows:
        try:
            r["value"] = float(r["metric"])
        except (TypeError, ValueError):
            r["value"] = None
    return rows


def running_best(rows, lower):
    xs, ys, cur = [], [], None
    for i, r in enumerate(rows, 1):
        if r["status"] in ("keep", "baseline") and r["value"] is not None:
            if cur is None or (r["value"] < cur if lower else r["value"] > cur):
                cur = r["value"]
        if cur is not None:
            xs.append(i)
            ys.append(cur)
    return xs, ys


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tsv")
    p.add_argument("-o", "--out", default="progress.png")
    p.add_argument("--lower", action="store_true", help="lower metric is better")
    p.add_argument("--title", default="Experiment progress")
    p.add_argument("--ylabel", default="metric")
    p.add_argument("--no-labels", action="store_true")
    p.add_argument("--label-chars", type=int, default=28)
    a = p.parse_args(argv)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("plot_progress.py needs matplotlib (pip install matplotlib)")

    rows = load(a.tsv)
    if not rows:
        sys.exit(f"{a.tsv}: no rows")
    vals = [r["value"] for r in rows if r["value"] is not None]
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    ax.set_axisbelow(True)

    other = [(i, r["value"]) for i, r in enumerate(rows, 1)
             if r["value"] is not None and r["status"] not in ("keep",)]
    kept = [(i, r) for i, r in enumerate(rows, 1) if r["status"] == "keep" and r["value"] is not None]
    if other:
        ax.scatter(*zip(*other), s=22, color=GREY, zorder=2, label="discarded or baseline")
    bx, by = running_best(rows, a.lower)
    if bx:
        ax.step(bx, by, where="post", color=GREEN, linewidth=2, zorder=3, label="running best")
    if kept:
        ax.scatter([i for i, _ in kept], [r["value"] for _, r in kept], s=46, color=GREEN,
                   edgecolor="white", linewidth=1.5, zorder=4, label="kept")
    crashes = [i for i, r in enumerate(rows, 1) if r["status"] == "crash" or r["value"] is None]
    if crashes and vals:
        span = (max(vals) - min(vals)) or 1.0
        lo = min(vals) - 0.06 * span  # below every real point, so crashes never hide a result
        ax.scatter(crashes, [lo] * len(crashes), marker="x", s=18, color=MUTED, zorder=2,
                   label="crash / no result")

    if not a.no_labels:
        for k, (i, r) in enumerate(kept):
            text = (r.get("description") or r["id"])[: a.label_chars]
            dy = 10 if k % 2 == 0 else -14
            ax.annotate(text, (i, r["value"]), xytext=(4, dy), textcoords="offset points",
                        fontsize=7.5, color=INK, rotation=0)

    n_keep = len(kept)
    ax.set_title(f"{a.title}: {len(rows)} experiments, {n_keep} kept", loc="left", fontsize=12, color=INK)
    ax.set_xlabel("experiment #", color=MUTED)
    ax.set_ylabel(a.ylabel + (" (lower is better)" if a.lower else " (higher is better)"), color=MUTED)
    ax.set_xlim(0, len(rows) + 1)
    ax.legend(frameon=False, fontsize=8, loc="lower right" if not a.lower else "upper right")
    fig.tight_layout()
    fig.savefig(a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
