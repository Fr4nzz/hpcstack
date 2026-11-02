# Contributing

Thanks for helping. The most useful contributions are site profiles, measured examples, and fixes to advice
that turned out wrong on a real cluster.

## Before you open a pull request

```bash
scripts/lint.sh          # frontmatter, line budgets, bash -n, shellcheck, py_compile, JSON, em dashes
scripts/scrub_check.sh   # privacy patterns (see "Scrub rules" below)
```

Both must pass. To run them on every commit, enable the hook once per clone:
`git config core.hooksPath .githooks`. CI runs the same checks on every push and pull request.

## Adding a site profile

1. Copy `sites/TEMPLATE.md` to `sites/<site>-<cluster>.md`.
2. Fill it only with facts that are public (your centre's public docs, nf-core/configs, published papers) or
   that any user can check with read-only commands (`bqueues -l`, `sinfo`, `lfs df`). Say which.
3. Date anything that changes: queue limits, known-bad hosts, module versions. Add "re-verify after 30 days"
   to host lists.
4. Leave out internal wiki text, quota levels, other users' usage, and login or authentication endpoints.
   Link to your centre's own documentation instead.

## Adding or changing a skill

- `skills/<name>/SKILL.md` starts with YAML frontmatter: `name` equal to the folder name, and a `description`
  that says when to use it ("Use when the user says..."). The description is always in the agent's context,
  so it is a trigger, not a summary.
- Keep the body under about 150 lines. Put detail in `references/` and runnable helpers in `scripts/`.
- State the goal, the done-state, the proof, the budget and the stop condition. Prefer a bad/good example
  pair over a paragraph of rules.
- Add `agents/openai.yaml` (Codex display name, short description, default prompt) and list the skill in
  `.claude-plugin/plugin.json`.
- Scripts must be small, documented in their header, and runnable with `--help`. Mark anything that has not
  been run on a real cluster as UNTESTED in both the script header and the `SKILL.md`.
- Claims about savings need numbers you measured. Say what was measured and what was estimated.

## Scrub rules

This repo is public. Before committing:

- No user names, uids, group or team names, home or scratch paths, host names, job ids, session ids, or
  project and dataset names. Use `$USER`, `<group>`, `$SCRATCH/<project>`, `<login-node>`, `<bad-host>`.
- No quota levels, incident dates with names, fairshare lines or scheduler config dumps. Keep the shape of an
  incident ("a team hit its file limit, one user held 0.4 %") and drop the identifiers.
- Results tables show speed-ups and resource numbers, not unpublished scientific metrics.
- No chat transcripts or agent session folders.

`scripts/scrub_check.sh` enforces the generic part of these rules with built-in patterns. Personal patterns
(your own user name, group, paths) belong in a local file that is never committed: put one extended regex per
line in `.scrub-patterns.local` at the repo root (it is in `.gitignore`), or point `HPCSTACK_SCRUB_PATTERNS`
at a file elsewhere. In CI, write that file from a secret.

## Style

Plain, direct sentences. No em dashes. Avoid semicolons in prose. One term per concept.
