# Optimization log: <pipeline step>

Written before the first measurement. Sections 1 to 3 do not change after measuring starts. Section 4 is
append-only.

## 1. Baseline that must not change

| Item | Value |
|---|---|
| input slice | path, size, SHA-256 |
| tool and library versions | |
| parameters and seeds | |
| hardware and allocation | queue, cores, memory, GPU type |
| metric | e.g. median s/step after 30 warm-up steps, items/hour, peak RSS |
| baseline value | measured N times: mean, spread |

## 2. Targets and budget

- Target: <e.g. at least 2x faster per item, peak RSS under 10 GB, fits in 6 cores>
- Budget: <e.g. 4 GPU-hours, 1 day>
- Stop when: target met, budget spent, or ceiling gap below 10 %.

## 3. Acceptance rules

| Class | Verified by | Keep if |
|---|---|---|
| E exact | identity check (`cmp`, sorted `md5sum`, `array_equal` on N items) | at least 5 % time or 10 % memory |
| W scheduling | output equal up to order or seed | at least 5 % time or 10 % memory |
| D distribution | pre-registered test: <metric> within max(2 x seed spread, <floor>) | at least 15 % time or 25 % memory, and test passes |
| Recipe | separate full run, user approval | never folded in silently |

Noise floor of the speed metric: <spread of 3 baseline runs>.

## 4. Hypothesis log (append-only)

| # | Date | Idea | Class | Expected effect | Measured effect | Evidence | Decision |
|---|---|---|---|---|---|---|---|
| 1 | | Set BLAS threads to 1 per worker | E | 20-40 % faster | | identical first 2,000 items | |
| 2 | | | | | | | |

## 5. Result

Final settings, before and after on the same bench, the new resource request, and what was not verified.
