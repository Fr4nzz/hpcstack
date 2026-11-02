# Example: an experiment loop for a specimen sex classifier

A case for `cluster-autoresearch`. The task was a binary classifier that predicts the sex of museum
specimens from photographs. It started as small heads on features from a large frozen image encoder. Over
one week, an agent ran 87 registered experiments in waves, first for accuracy, then for speed.

Result: **+4.8 percentage points (pp) of development accuracy and 18.6 s to 0.20 s per specimen** (about
90x faster). On the locked test set, opened only through logged confirmations, the gain held (+4.2 pp for
the first candidate, +5.8 pp for the final model, both against the released recipe).

## Method

- **Fixed and pinned:** the evaluation code and the data-access code were SHA-256 pinned. One editable
  training file. A proposal step registered every idea (hypothesis, expected gain, cost) before it ran.
- **Metric:** accuracy on a development split, 3 repeats of grouped 5-fold cross-validation, 3 seeds
  averaged (60 fits per experiment). Five baseline replicates gave a seed spread of 0.06 pp.
- **Keep rule:** gain of at least 0.30 pp, a paired bootstrap 95 % interval above zero, and guard metrics
  (accuracy on hard subgroups and on unseen groups, precision and coverage of confident predictions) not
  worse. Ties went to the simpler option.
- **Locked test set:** opened only through an explicit, logged `confirm` step, a handful of times in total.
- **Compute:** experiments went into a file queue drained by batch worker jobs (`--idle-min 25`, a
  `--max-hours` cap, a `STOP` file). No interactive allocation was left idling between waves.
- **Critique before compute:** four ideas were parked because their expected gain was below the noise floor.

## How the main line moved (development accuracy)

| Step | Change | Delta |
|---|---|---|
| 1 | high-resolution tiles of a diagnostic region added to the inputs | +0.97 pp |
| 2 | one output head per input type instead of one shared head | +1.66 pp |
| 3 | small CNN (MobileNetV3-Large, 4.2 M parameters) fine-tuned end to end, replacing the frozen encoder | +1.73 pp |
| 4 | random dropout of whole input crops during training | +0.50 pp |
| 5 | fewer, larger crops of the diagnostic region (chosen for speed and simplicity) | -0.09 pp, within noise |

About 9 in 10 experiments were discarded. A speed wave in between tried fewer crops with the frozen encoder.
It halved the time per specimen at a cost of 0.7 pp, and was later superseded by the CNN.

`plot_progress.py` draws this history as grey discarded points, green kept points and a running-best
step line.

## The attribution lesson

Several ideas stacked, and the first story was wrong.

- After the switch to the small CNN, the tempting conclusion was "a smaller model was better". It wasn't.
  Two smaller **frozen** encoders on the same inputs were worse than the large frozen one (-0.4 pp and
  -2.2 pp). The gain came from **fine-tuning** a small CNN on task-specific crops, not from size.
- Attribution depends on order. Measured along the main line, the input and head changes carried 56 % of
  the development gain, the CNN 36 % and crop dropout the rest. In a CNN-first ordering the switch alone was
  worth about +2.6 to +3.3 pp and the high-resolution tiles about +1.4 pp.
- So before writing "X caused the gain": ablate the final stack one idea at a time, or replay the main line
  in a different order, and test the obvious confound (here: frozen versus fine-tuned).

## Speed

Time per specimen went from 18.6 s (frozen large encoder, 12 crops) to 8.6 s (same encoder, 6 crops) to
0.20 s (fine-tuned small CNN, final input set), measured on a small 2-thread CPU host. Some per-recipe
timings were estimated from per-crop costs, so treat the middle values as approximate.

## What to copy

- Pin the evaluation and register ideas first. It made the 9-in-10 discard rate cheap to accept.
- A keep rule with a noise floor stopped the loop from chasing 0.1 pp wiggles.
- A file-queue worker with an idle exit meant an agent that stopped did not leave hardware idle.
- Change the question explicitly (accuracy, then speed) and say which metric each wave optimises.
