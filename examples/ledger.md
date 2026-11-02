# Resource ledger (template)

Append one row per verified run. Agents read this before sizing a job, so nobody repeats a smoke test that
was already done. Keep it in your project, not in this repo. The two example rows use numbers from the
examples in this folder. Unknown values are left as `-`.

| Date | Task | Input size | Queue | Cores | -M (MB) | GPU | Wall | CPU eff | Peak mem | Throughput | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| (example) | align one sample (bwa-mem2, 4 threads) | - | normal | 4 | 16000 | none | 1.7 h (median) | 0.82 | 6.2 GB | - | jobeff --lsf-log |
| (example) | segmentation training, 1280 px tiles | - | gpu | 6 | 20000 | 1 x A100 | - | - | 9.8 GB host | 0.57 s/step | optimize-pipeline log |
| | | | | | | | | | | | |

Rules:

- Only verified runs (exit 0 and outputs checked). Failed runs go in the notes of the job, not here.
- Peak memory is what the scheduler measured. Note if the job hit its limit (then the true peak is unknown).
- When a row is superseded (new tool version, new input type), add a new row. Don't edit old ones.
