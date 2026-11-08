# program.md: `<study name>`

Approved by the user on `<date>`. Changing sections 1 to 4 needs the user's approval again.

## 1. Question and metric

- Question: `<what to improve, in one sentence>`
- Primary metric: `<name>`, `<higher or lower>` is better, computed by `eval.py`
- Development data: `<split, size>`. Every keep or discard decision uses it.
- Locked data: `<split, size>`. Opened only through `confirm`, with user approval, logged, at most `<count>` times.
- Guard metrics that must not drop by more than `<tolerance>`: `<list>`

## 2. Files

| File | Role | SHA-256 |
|---|---|---|
| `eval.py` | evaluation | `<hash>` |
| `data.py` | data access and splits | `<hash>` |
| `keep_rule.py` | decides keep or discard | `<hash>` |
| `train.py` | **the one editable file** | (changes per experiment) |

Check hashes at the start of every wave with `sha256sum -c pinned.sha256`. A mismatch stops the loop.

## 3. Keep rule

- Baseline: `<replicates>` replicates, mean `<mean>`, seed spread `<spread>`.
- Keep if the mean gain over the incumbent, on the same seeds, is above max(2 x `<spread>`, `<floor>`) and every
  guard holds.
- Ties within the noise go to the simpler option (less code, less compute).
- A kept change is replicated with fresh seeds before the next wave builds on it.

## 4. Budget and stop rule

- Total: `<GPU-hours or core-hours>`. Wall-clock end: `<date and time>`.
- Per wave: `<8 to 12>` runs, about `<GPU-hours>` GPU-h. Concurrency cap: `<jobs>`.
- Stop when the budget is spent, the end time is reached, or `<waves>` waves in a row have no keep.
- Ask the user before changing the metric or data, opening locked data, or exceeding the budget.

## 5. Idea register (append-only)

| id | wave | hypothesis | expected gain | falsifier | cost | status |
|---|---|---|---|---|---|---|
| w01_example | w01 | `<why it might help>` | +0.3 | no gain over 3 seeds | 0.5 GPU-h | registered |

## 6. Outputs

- `results.tsv`: id, metric, cost, status, description (via `results_tsv.py`)
- `progress.png`: via `plot_progress.py`
- `FINDINGS.md`: 2 to 5 lines per wave
