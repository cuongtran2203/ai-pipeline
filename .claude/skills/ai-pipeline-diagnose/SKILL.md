---
name: ai-pipeline-diagnose
description: Domain-agnostic diagnosis of any weak component of an AI pipeline (vision, NLP, speech, tabular, time series, recommenders, multi-stage systems). A weakness-diagnostician agent verifies WHY a component misses its target - data, model, structure/auxiliary module, objective/metric, or label noise - with discriminating experiments and picks a branch of a playbook the human approved at G2; the architect drafts that playbook (flow + diagram) at planning time.
---

# Diagnose weak components, pre-plan the response (any AI project)

The examples in this file (cells, characters, small objects…) are illustrations. The method is modality-neutral: **find the cheapest experiment that separates the hypotheses, never fix by intuition.** Two parts: **(A)** a playbook approved at planning, **(B)** an agent that diagnoses a weak component and selects a playbook branch.

## A. Playbook at planning (G2)
The architect writes `runs/<id>/playbook.json` (template: `templates/playbook.template.json`, filled example: `examples/playbook.sample.json`) and renders it with `python scripts/playbook_diagram.py runs/<id>/playbook.json` → `playbook.html` (inline SVG diagram + table, light/dark) and `playbook.md` (Mermaid + short explanation).
Contents: the **pipeline flow** (any graph of stages: data → pre-process → model(s) → post-process → serving), per component its **target metric, risk (low/med/high) and why it may be weak**, the **cheap checks to run first**, the **pre-planned action per verdict** (DATA / MODEL / STRUCTURE / OBJECTIVE / NOISE) with a **cost cap**, and candidate **auxiliary modules**. The human approves diagram + actions at **Gate G2**; actions inside the caps need no new approval, anything outside asks again.

## B. Trigger and agent
Trigger (coordinator): a component's metric is below target by more than its measurement resolution (≥ ~½·SE) after its first real training round; or probe/ceiling work (`ai-pipeline-feasibility` C1) flags it; or two optimize rounds in a row miss the predicted gain. Start one `weakness-diagnostician` task per weak component (code group; container only; train/val/cross-fit only, never the locked test). Its report feeds the next round's hypothesis and `ceiling.json`.

## Verdict taxonomy (five root causes)
| Verdict | Meaning | Typical signs |
|---|---|---|
| **DATA** | too little real data; coverage gaps / long tail; train–eval shift; synthetic or augmented data ≠ real distribution | learning curve still falling; train ≫ eval; errors concentrated in under-represented slices; synth-trained model fails on real |
| **MODEL** | capacity, scale or optimisation limit: the target is small/subtle relative to the input, resolution/stride/receptive field too coarse, context too short, backbone too weak, recipe not converged | cannot overfit a small subset; errors vs size/length/frequency curve; one step up in size/resolution/context moves the metric |
| **STRUCTURE** | the task needs a different decomposition or an **auxiliary model/module**: split a composite input into parts, localise first then classify, add a retrieval/re-ranker/normaliser, separate stages, better pre/post-processing | **oracle** (ground truth for an upstream/sub-step) lifts the metric a lot; error decomposition shows one stage dominates; whole-vs-part read differs |
| **OBJECTIVE** | metric/threshold/loss/spec misaligned with what is measured: calibration, thresholds, label schema, metric definition, normalisation | metric changes a lot with threshold/normalisation; ranking OK but decision rule wrong; spec ambiguity |
| **NOISE** | labels ambiguous or wrong, so the ceiling is below target | independent re-annotation disagrees at ≈ the error rate; duplicates with conflicting labels |
Verdict may be MIXED (give shares). Evidence per test is mandatory.

## Discriminating tests (cheapest first; pick those that apply to the modality)
| Hypothesis | Generic test | Reads as |
|---|---|---|
| DATA – quantity | learning curve (10/25/50/100 % of train data, ≥3 points, ≥2 seeds), fit `err(n)=a·n^-b+c`; train–eval gap | steep curve + big gap ⇒ data-limited |
| DATA – coverage | metric per slice (class, length, source, device, speaker, language, time, size bucket, difficulty); frequency vs error | worst slices = rare slices ⇒ coverage/long tail |
| DATA – shift | domain-classifier or embedding distance train vs eval; matched-slice comparison; feature/label histograms | separable domains / worse than matched train slice ⇒ shift |
| DATA – synthetic ≠ real | train synth→eval real and real→eval synth; ablation "real only" vs "+synthetic/augmented" on a real val set | adding synthetic hurts or does not help ⇒ generator mismatch: do not generate more of the same |
| MODEL – capacity/optimisation | overfit a small subset (train metric must reach ~100 %); training-curve inspection; one-step-up size / resolution / context / schedule | cannot overfit ⇒ capacity/optimisation; improves with one step up ⇒ scale-limited |
| MODEL – scale/granularity | error vs object size / sequence length / duration; pixels-per-target vs stride & receptive field; token budget vs input length | errors concentrate on the small/long end ⇒ resolution/context limit |
| STRUCTURE – oracle ladder | replace each upstream stage with ground truth, one at a time (GT boxes, GT segments, GT retrieval, GT normalisation) and re-measure; error decomposition by stage | oracle jump ⇒ build/improve that stage or an auxiliary module; no change ⇒ not the bottleneck |
| STRUCTURE – whole vs part | compare reading/predicting the whole unit vs its parts (parts then merge) | parts clearly better ⇒ decomposition helps |
| OBJECTIVE | sweep threshold/calibration/normalisation; recompute with the alternative metric definition | large swing ⇒ objective/spec issue |
| NOISE | blind re-annotation of 100–200 items by an independent reader (human preferred); duplicate/near-duplicate conflicts; `ai-pipeline-feasibility` ceiling | disagreement ≈ error ⇒ noise floor, retarget |

### Modality adapters (what "size/part/slice" means)
- **Vision (classification/detection/segmentation/OCR/KIE):** object-to-image ratio and pixels per target vs stride; per-class/size/lighting/device slices; GT box/mask oracle; crop-and-classify vs end-to-end; resolution and neck/FPN change; for composite fields: split into parts.
- **NLP / LLM:** length and domain/vocabulary slices, label-schema ambiguity, retrieval oracle (gold passages), tokenisation/normalisation, prompt vs fine-tune ablation; context-length limit.
- **Speech/audio:** speaker/accent/noise/length slices, VAD/segmentation oracle, sample-rate/feature resolution.
- **Tabular / time series / recommenders:** leakage check, feature coverage, drift/time-split performance, cold-start slice, candidate-generation oracle vs ranker, calibration.
- **Multi-stage / agentic systems:** per-stage success and oracle chain; failure attribution to retrieval, planning, tool, or generation.

## Verdict and action
`verdict` with estimated shares and the evidence per test, then the playbook branch:
- **DATA** → request real/original data from the human, targeted collection of the worst slices, relabel/clean; synthetic only if the synth↔real test shows it matches, always ablated on a real val set.
- **MODEL** → customise/improve the model: input resolution or crop, stride/neck/receptive field for small targets, longer context, bigger or task-specific backbone, loss/recipe fix; one change per round with predicted gain.
- **STRUCTURE** → add the auxiliary model/module the oracle justified (splitter/segmenter, localiser, re-ranker, normaliser, better pre/post-processing): define its interface, owner dir, acceptance and add it to the plan as a new parallel-worktree task.
- **OBJECTIVE** → fix metric/threshold/normalisation/spec with the human (it changes what "target" means: record in `decisions.md`).
- **NOISE** → retarget or accept the gap (`ai-pipeline-feasibility`), report honestly.
Every action carries `predicted_gain`, cost and how it is measured (hypothesis rule of `ai-pipeline-feasibility`); a branch outside the playbook or over its cost cap ⇒ ask the human.

## `diagnosis.json`
```json
{"component":"<id>","metric":"<name>","current":0.0,"target":0.0,"n":0,
 "tests":[{"hypothesis":"DATA|MODEL|STRUCTURE|OBJECTIVE|NOISE","test":"learning curve","result":"…","supports":false}],
 "verdict":"STRUCTURE","shares":{"DATA":0.2,"MODEL":0.1,"STRUCTURE":0.6,"OBJECTIVE":0.0,"NOISE":0.1},
 "actions":[{"branch":"STRUCTURE","do":"add module …","predicted_gain":0.03,"cost":"2 tasks, ~1 GPU-day","measure":"metric ≥ …"}],
 "outside_playbook":false}
```
(Older runs may use the verdict name `AUX` for STRUCTURE.)

## Presenting to the human
Always give the diagram (`playbook.html`) plus 3–5 lines per component: *what the checks showed → verdict → proposed action → predicted gain/cost*. The orchestrator asks for approval only for actions outside the approved playbook or above its cost caps.
