---
name: ai-pipeline-diagnose
description: When a pipeline component scores below target, a weakness-diagnostician agent verifies WHY (data / model capacity / auxiliary module needed / label noise) with discriminating experiments and picks the branch of the pre-approved playbook; at planning time the architect drafts the playbook (flow + diagram) for the human to approve at G2.
---

# Diagnose weak components, pre-plan the response

Two parts: **(A)** a playbook written at planning time and approved by the human, **(B)** an agent that diagnoses a weak component and selects a branch of that playbook.

## A. Playbook at planning (G2)
The architect writes `runs/<id>/playbook.json` (example: `examples/playbook.sample.json`) and renders it:
`python scripts/playbook_diagram.py runs/<id>/playbook.json` → `playbook.html` (inline SVG diagram + table, light/dark) and `playbook.md` (Mermaid + short explanation).
It contains: the pipeline flow, each component with its target and risk (low/med/high) and *why it may be weak*, the cheap checks to run first, and for each component the pre-planned action per verdict **DATA / MODEL / AUX / NOISE** with cost caps, plus candidate auxiliary modules (e.g. split a 6-cell date into per-cell digits). **The human approves the diagram and the actions at Gate G2**; actions inside the approved caps need no new approval, anything outside asks again.

## B. Trigger and agent
Trigger (coordinator): a component's metric is below target by more than its measurement resolution (≥ ~½·SE) after its first real training round, or B0/C1 flags it, or two optimize rounds in a row miss the predicted gain. Start one `weakness-diagnostician` task per weak component (`role: weakness-diagnostician`; code group; its own worktree if it writes code; container only; train/val/OOF only, never test). The diagnosis report feeds the next round's hypothesis and `ceiling.json`.

## Discriminating tests (cheapest first)
| Hypothesis | What to measure | Reads as |
|---|---|---|
| **DATA – too little real data** | learning curve (10/25/50/100 % real data, ≥3 points, ≥2 seeds); train vs val gap | curve still falling steeply and train≫val ⇒ data-limited |
| **DATA – synthetic ≠ real** | train on synth only → eval on real val; train on real only → eval on synth; embedding / simple domain-classifier separability synth vs real; per-slice gaps (profile, degrade, writer) | big real–synth gap or separable domains ⇒ generator mismatch: more synth of the same kind will not help (check the ablation: synth added vs real only) |
| **DATA – train/val shift** | same metrics on train-like vs val slices; class/length/format histograms | val slice much worse than matched train slice ⇒ distribution shift |
| **MODEL – capacity/size** | overfit a ~500-sample subset (train acc < ~99 % ⇒ capacity/optimisation); one step up in backbone size / input resolution / stride; error rate vs object size (object/background ratio, pixels per character vs feature-map stride/receptive field) | errors concentrate on small objects, or a bigger input/backbone moves the metric ⇒ model/input resolution limit |
| **AUX – structure needs a helper** | **oracle experiments**: feed ground-truth localisation/segmentation (e.g. GT cell or character boxes) into the next stage and re-measure; decompose the error by stage (localisation vs recognition vs post-process); compare whole-field read vs per-part read | oracle jumps the metric ⇒ a helper that does that step (segmenter, cell splitter, aligner, detector refinement) is worth building; if oracle changes nothing ⇒ not the bottleneck |
| **NOISE – labels** | blind re-read of 100–200 samples; disagreement and "unsure" rate; duplicate/near-duplicate items with different labels; ceiling from `ai-pipeline-feasibility` | disagreement ≈ the error ⇒ noise floor; retarget instead of training |

## Verdict and action
`verdict` = DATA | MODEL | AUX | NOISE | MIXED with estimated shares and the evidence per test. Then choose the playbook branch:
- **DATA** → real/original data request to the human, relabeling/cleaning, targeted collection; synthetic only if the real-vs-synth test shows the generator matches (and always with an ablation on val).
- **MODEL** → customise/improve the model: higher input resolution or crop, different stride/neck for small objects, bigger or task-specific backbone, loss/recipe change; one change per round with predicted gain.
- **AUX** → add the auxiliary model/module the oracle justified (e.g. per-cell digit classifier + cell segmenter for a 6-cell date, time-range splitter); define its interface, owner dir, acceptance, and put it in the plan as a new task (parallel worktree).
- **NOISE** → retarget or accept the gap (`ai-pipeline-feasibility` decision `retarget`), report honestly.
Each action carries `predicted_gain`, cost, and how it will be measured (hypothesis rule of `ai-pipeline-feasibility`).

## `diagnosis.json`
```json
{"component":"date","metric":"EM val","current":0.938,"target":0.99,"n":65,
 "tests":[{"hypothesis":"DATA","test":"learning curve","result":"…","supports":false}],
 "verdict":"AUX","shares":{"DATA":0.2,"MODEL":0.1,"AUX":0.6,"NOISE":0.1},
 "actions":[{"branch":"AUX","do":"per-cell digit classifier + cell segmenter","predicted_gain":0.03,"cost":"2 tasks, ~1 GPU-day","measure":"date EM val ≥ 97 %"}],
 "outside_playbook":false}
```

## Presenting to the human
Always give the diagram (`playbook.html`) plus 3–5 lines per component: *what the checks showed → verdict → proposed action → predicted gain/cost*. The orchestrator asks for approval only for actions outside the approved playbook or above its cost caps.
