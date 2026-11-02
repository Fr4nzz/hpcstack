# hpcstack

Agent skills for shared clusters: faster, cheaper, and fairer to everyone else in the queue.

These skills teach a coding agent (Claude Code, Codex, or anything that reads `SKILL.md`) how to work on a
shared batch cluster the way a careful colleague would. They submit jobs that ask for what they use, measure
before they optimise, clean up after themselves, and stop when their budget is spent. LSF is the primary
scheduler and SLURM gets the same treatment in every skill.

They were written on a large LSF genomics and imaging cluster, from real jobs that wasted real core-hours.
The `examples/` folder shows the measured cases.

## Skills

| Skill | Say | What it does |
|---|---|---|
| `cluster-ops` | "run this on the cluster" | Submit, monitor and recover jobs. The interactive-versus-batch rule. LSF and SLURM. |
| `right-size-jobs` | "how much memory should I ask for?" | Reads bjobs/bacct/sacct, reports efficiency and the next request. |
| `optimize-pipeline` | "make this faster" | Profile, pre-register hypotheses, separate exact from distribution-changing changes, measure. |
| `cluster-autoresearch` | "run experiments overnight" | A budgeted keep-or-discard loop with `results.tsv` and `progress.png`. |
| `safe-interactive` | "give me a GPU shell" | An interactive allocation that releases itself when you stop. |
| `storage-footprint` | "what can I delete?" | Inode-first disk audit that doesn't hammer the metadata server. |
| `genomics-jobs` | "run bwa on 500 samples" | Arrays, thread flags, Java heap versus `-M`, no small-file floods. |
| `fair-use-preflight` | "before I submit" | A 60-second fairness checklist that ends with a receipt. |
| `nextflow-on-lsf` | "run nf-core on the farm" | Profiles, labels, retries, work-dir hygiene, sizing from the trace. |

Several skills hand off to each other. `fair-use-preflight` calls `right-size-jobs`, `cluster-ops` sends
interactive work to `safe-interactive`, and `optimize-pipeline` ends in `right-size-jobs`. Install them all.

## Install

**Claude Code (plugin)**
```bash
claude plugin marketplace add <owner>/hpcstack
claude plugin install hpcstack@hpcstack
```

**Codex (plugin)**
```bash
codex plugin marketplace add <owner>/hpcstack
codex plugin add hpcstack@hpcstack
```

**Any agent via skills.sh**
```bash
npx skills@latest add <owner>/hpcstack     # choose the agent interactively, or pass -a <agent>
```

**Manual copy or symlinks**
```bash
git clone https://github.com/<owner>/hpcstack
cp -R hpcstack/skills/* ~/.claude/skills/   # Claude Code
cp -R hpcstack/skills/* ~/.codex/skills/    # Codex
# or keep a checkout and symlink it, so `git pull` updates the skills:
hpcstack/scripts/link-skills.sh             # see --help for targets
```

Pick one method per agent. Installing twice gives you every skill twice.

The helper scripts need `bash` and `python3` (3.8 or newer). `plot_progress.py` also needs `matplotlib`.

## Tell the agent where it is

Skills stay generic. Cluster facts live in a site profile, and your personal values live in your own agent
instructions. Add something like this to `CLAUDE.md` or `AGENTS.md`:

```text
Cluster: <site> (see hpcstack sites/<site>.md). Scheduler: LSF.
My group: <group>. Scratch: /path/to/scratch/<group>/<user>. Login node: <login-node>.
```

`sites/` holds a template and one example profile. If your cluster has no profile, the skills run a short
discovery (`lsid` or `sinfo`, `bqueues`, `lfs df`, `mount`) and ask you to confirm the result. Pull requests
with new site profiles are welcome (see `CONTRIBUTING.md`).

## Philosophy

- **Set goals and guardrails, not recipes.** A skill says what done looks like, what must never happen, how
  much it may spend and when to stop. It leaves the method to the agent. Over-constrained skills block the
  good ideas that a capable model brings.
- **Verify with evidence.** "DONE" is a scheduler state, not a result. Every skill asks for proof observed in
  the session: exit codes, log tails, checksums, counts, a before-and-after measurement. Anything the agent
  could not confirm gets listed as such.
- **Measure, then change.** Most waste we found came from boring causes that profiling found and guessing
  did not: a tool using one core out of two, a JVM sizing itself for the whole node, a thread pool per worker.
- **Be a good neighbour.** A shared cluster is a commons. Ask for what you use, cap your arrays, keep scans
  off the login node and off the metadata server, release idle allocations, and state the cost before you
  spend it.
- **Short skills, detail on demand.** Each `SKILL.md` stays under about 150 lines. Its description is a
  trigger ("Use when..."), and longer material sits in `references/` for the agent to open when needed.

## Status

Version 0.1. The skills come from real use, but the helper scripts are new:

- `safe-interactive/scripts/hpc-session` was tested on real LSF jobs (CPU and GPU queues): it stays out of
  the way while the job works or the user types, and releases the job once both stop. A busy GPU with idle
  CPU keeps the job (tested). Not yet tested on SLURM.
- `jobeff` and `quota_report.sh` were run against live LSF and Lustre. `queue_worker.py` ran LSF batch
  workers: failing, timed-out and crashing tasks were recorded without stopping the queue, the worker exited
  0 and its `done()` dependent ran. A worker signalled with USR2 exited 140, so its `done()` dependent never
  fired. `listing_job.sh`, `analyse_listing.py`, `results_tsv.py` and `plot_progress.py` were tested on synthetic
  inputs (fake `lfs`, local directories), not yet on a cluster. SLURM paths are untested.

Reports of what breaks on your cluster are the most useful issues you can open.

## Make them yours

These are starting points. Edit them. Keep personal settings (group, scratch path, preferred queues) in your
`CLAUDE.md` or `AGENTS.md` so you can update the skills without losing them.

## License

[MIT](LICENSE)
