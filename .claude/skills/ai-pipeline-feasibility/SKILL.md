---
name: ai-pipeline-feasibility
description: Estimate the achievable ceiling of an AI task (e.g. target 99% but reality may cap at 95%) at fixed checkpoints and decide proceed / retarget / stop, so agents never loop on an unreachable target. Use after analysis, after the first baseline, and in every optimize round.
---

# Feasibility & ceiling — know the limit before burning rounds

The ceiling is a **range** (lower–upper), never a point, and it is only as good as its evidence. Output: `runs/<id>/ceiling.json` + `feasibility.md` (Vietnamese). Role: `roles/feasibility-analyst.md`.

## What limits the score (inputs to collect)
| Limit | Evidence to gather | Cost |
|---|---|---|
| **Label noise / ambiguity** (irreducible error) | second annotator on ≥100–200 random test items (disagreement ≈ floor); duplicates or near-duplicates with conflicting labels; confident-learning style flagging from cross-val predictions | cheap, no training |
| **Train↔test shift** | A1 distribution comparison (source, quality, classes, difficulty) | done in analysis |
| **Prior art** | published results on same/similar benchmark and data size (researcher, A3) | cheap |
| **Data-limited vs capacity-limited** | learning curve: train at 10/25/50/100% of data (≥3 points, ≥2 seeds), fit `err(n) = a·n^-b + c`; `c` = floor for this model family. Capacity probe: overfit a ~500-sample subset (train acc <99% ⇒ optimization/capacity problem); then train-vs-val gap (large ⇒ data/regularization, small ⇒ capacity) | minutes–hours |
| **Error composition** | cluster errors; share that is noisy label / ambiguous / fixable (I2 did 18+39 of 159 on MNIST) | after a first model |
| **Measurement resolution** | val/test standard error `sqrt(p(1-p)/n)`; improvements below ~½ SE are noise | free |
| **Real-data gap** | with no real labeled eval (≥50), production ceiling is **unknowable**: state it | — |

## Checkpoints (when a ceiling can be estimated)
- **C0 — after analysis, before the debate/plan** (no training): data audit + prior art + noise-floor sample ⇒ coarse band. If `target > upper` ⇒ raise at **G2** (human decides: lower target, get more/cleaner data, change scope). The architect must see C0.
- **C1 — first build task, a cheap baseline probe** (`phase: probe`; tiny model, minutes): learning curve + capacity probe ⇒ refined band and diagnosis (data- vs capacity-limited). Gate by the coordinator: if `target > upper` ⇒ ask the human before any full training.
- **C2 — every optimize round**: before the round, each hypothesis states `predicted_gain`; after, record `measured_gain`; re-fit the band with the new point; re-audit error composition.
- **C3 — final report**: achieved vs band vs target, with the evidence.

## Decision rules (coordinator applies, recorded in `ceiling.json`)
- `proceed`: target ≤ lower, or inside the band with a hypothesis whose predicted gain ≥ remaining gap.
- `retarget` (ask human): target > upper. Offer: new target = band, collect data/labels, change model class, or accept the gap.
- `stop` the loop: any of — last 2 rounds each gained < max(½·SE, 0.1 pt); best remaining hypothesis predicts less than the gap; ≥ X % (default 50) of remaining errors are audited as noisy/ambiguous (floor reached); round/budget cap hit (default 3 rounds).
- Never start a round without a written hypothesis + predicted gain + how it is measured. A round that cannot name its mechanism is not allowed (keep it simple: data → pre/post-processing → training recipe → bigger model, in that order).
- Predicted vs measured gain off by >2× twice ⇒ the error model is wrong: pause and re-diagnose (error analysis), do not repeat the same kind of round.

Log every checkpoint (C0–C3) and round to the notebook (`experiment`/`insight`) and read prior entries before proposing a round.

## `ceiling.json`
```json
{"metric":"accuracy","target":0.99,
 "estimates":[{"checkpoint":"C0","lower":0.93,"upper":0.985,"basis":["label audit 200 items: 1.5% disagreement","A3: SOTA 0.99 on cleaner split"]}],
 "decision":"proceed|retarget|stop|ask","decision_note":"",
 "rounds":[{"round":1,"hypothesis":"...","predicted_gain":0.004,"measured_gain":0.0025,"se":0.0014}]}
```
`scripts/project_status.py` reads it: `upper < target` without a recorded human decision ⇒ blocked on the human.
