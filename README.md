# hpcstack

Agent skills for shared LSF clusters: faster, cheaper, and fairer to everyone else in the queue.

These skills teach a coding agent (Claude Code, Codex, or anything that reads `SKILL.md`) to work on a shared batch
cluster the way a careful colleague would. Jobs ask for what they use, measure before they optimise, clean up after
themselves, and stop when their budget is spent. The rules come from real jobs on one cluster.

**Tested on one cluster only:** the Wellcome Sanger Institute's farm22 (LSF). Nothing here has been tried on
another site or scheduler, so expect to adjust queue names, limits and paths. `sites/TEMPLATE.md` is the place to
start, and reports from other clusters are welcome.

## Skills

| Skill | Say | What it does |
|---|---|---|
| `cluster-ops` | "run this on the cluster", "why is my job pending?", "give me a GPU shell", "what can I delete?" | Submit, size, monitor and recover jobs. Interactive sessions that release themselves, a preflight checklist, inode-first storage audits, workflow managers. |
| `optimize-pipeline` | "make this faster", "why is this so slow?" | Profile, pre-register hypotheses, separate exact from distribution-changing changes, measure. |
| `cluster-autoresearch` | "run experiments overnight" | A budgeted keep-or-discard loop with `results.tsv` and `progress.png`. |

## Install

```bash
claude plugin marketplace add <owner>/hpcstack && claude plugin install hpcstack@hpcstack   # Claude Code
codex plugin marketplace add <owner>/hpcstack && codex plugin add hpcstack@hpcstack         # Codex
npx skills@latest add <owner>/hpcstack                                                      # other agents
```

Or clone the repo and run `scripts/link-skills.sh` (see `--help`) to symlink the skills, so `git pull` updates
them. Pick one method per agent: installing twice gives you every skill twice. The helper scripts need `bash` and
`python3` (3.8 or newer). `plot_progress.py` also needs `matplotlib`.

## Tell the agent where it is

Cluster facts live in a site profile (`sites/`), and personal values in your own `CLAUDE.md` or `AGENTS.md`:

```text
Cluster: <site> (see hpcstack sites/<site>.md). Scheduler: LSF.
My group: <group>. Scratch: /path/to/scratch/<group>/<user>. Login node: <login-node>.
```

Without a profile, the skills run a short read-only discovery and ask you to confirm the result.

## Principles

- Set goals and guardrails, not recipes: what done looks like, what must never happen, the budget, when to stop.
- "DONE" is a scheduler state, not a result. Every claim needs evidence from the session: exit codes, log
  tails, checksums, counts, a before-and-after measurement.
- Measure, then change. Most waste came from boring causes that profiling found and guessing did not.
- A shared cluster is a commons. Ask for what you use, cap arrays, keep scans off the login node, release idle
  allocations, and state the cost before you spend it.

## Status

Version 0.2. `xfer` (get, many, put) was tested on a real cluster through a Teleport proxy, with hashes checked.
Tested on real LSF jobs: `hpc-session` (CPU and GPU queues, including a busy GPU with idle CPU),
`hpc-probe` (a real `bsub -Is` on a CPU queue), `jobeff`, `quota_report.sh`, and `queue_worker.py` (failing,
timed-out and crashing tasks, `preempt`, idle exit, STOP, `bkill`, `bkill -s USR2`, one worker per scenario). Tested
only with fake commands or synthetic inputs: `pendlog`, `pend-risk`, `listing_job.sh`, `analyse_listing.py`,
`results_tsv.py`, `plot_progress.py`, and in `queue_worker.py` the resume on the next worker, the USR2 grace and the
`--est-min` fit check. Not yet tested: several workers sharing one queue. Reports of what breaks on your cluster are
the most useful issues you can open.

## Contributing

- Run `scripts/lint.sh`, `scripts/scrub_check.sh` and `uv run --with pytest python -m pytest -q` before a pull
  request. `git config core.hooksPath .githooks` runs the first two on every commit.
- A site profile starts from `sites/TEMPLATE.md` and holds only public or read-only-checkable facts.
- A skill's `SKILL.md` has frontmatter (`name` equal to the folder, a `description` that says "Use when..."),
  stays within its budget in `scripts/word-budget.json`, and has `agents/openai.yaml`. Detail goes in
  `references/`. Scripts run with `--help`, and anything not run on a real cluster is marked UNTESTED.
- This repo is public: no user names, group names, home or scratch paths, host names, job ids, quota levels or
  chat transcripts. Put your own patterns in `.scrub-patterns.local` (git-ignored), one regex per line.
- Plain sentences, no em dashes, no semicolons in prose, and placeholders such as `<queue>` in backticks.
  Savings claims need measured numbers.

## License

[MIT](LICENSE)
