---
name: ai-pipeline
description: Entry point for the AI-pipeline workflow. Use when the user gives a spec file describing an AI system to build (CV/NLP) and wants a plan plus multi-agent execution coordinated by Orca. Works in Claude Code and Codex.
---

# ai-pipeline — spec → plan → multi-agent execution

Input: a spec file path (see `templates/spec.template.md`). You are the **coordinator**. Workers are Orca-managed agents (claude or codex). Load `ai-pipeline-orca` before launching anything.

**Resuming / "dự án đang ở đâu?"**: if `runs/` already has a run (or the user asks for status), run `ai-pipeline-status` FIRST; it dispatches one assessor agent and tells which phase to execute next. Only then continue.

## Run directory
`runs/<run_id>/` (run_id = slug of spec name + date). Keep `spec.md` (copy), `plan.json`, `decisions.md`, `artifacts/<task_id>/`, `modules/<name>/`, `task_map.json`, `done.json`. Workers talk through these files only.

## Phases (each wave = parallel Orca workers)
1. **Intake** → `ai-pipeline-intake`. **Gate G1** (human) until the spec answers platform / input / output / dataset / targets.
2. **Analysis** (3 workers in parallel: data-analyst, requirements-analyst, researcher), then **F0 feasibility C0** (ceiling band before planning) → `ai-pipeline-analysis`, `ai-pipeline-feasibility`.
3. **Planning** (debate: 2 model-proposers on different agents + critic + architect/judge) → `ai-pipeline-planning`. Output `plan.json`. **Gate G2**: human approves plan and deployment method.
4. **Module build**: first a cheap **B0 baseline probe (C1)**, then one module-dev worker per module, parallel by DAG → `ai-pipeline-module-dev`. **Gate G3** before any training: GPU server, GPU type, CUDA, framework (or open-source link).
5. **Integration** (integrator, then error-analyst) → `ai-pipeline-integration`.
6. **Optimize loop**: each round = written hypothesis + predicted gain → fix → re-eval (C2). Stop by the rules in `ai-pipeline-feasibility` (target met, ceiling reached, diminishing returns, round cap 3 unless the user extends). If target > estimated ceiling, ask the human; never loop blindly.
7. **Release**: package for the chosen deployment, final report via `ai-pipeline-report`.

## Steps
1. `python scripts/validate_spec.py <spec> --json` → if questions remain, ask the human (G1), update the spec copy, re-validate.
2. Write the seed plan for phases 1–2 (structure of `examples/plan.sample.json`, without the build tasks). The architect task later emits the build-phase `plan.json`; run that build plan with the same `--run-dir` so the one Orca Run (`_run` in `task_map.json`) is reused, never replaced.
3. Preview: `python scripts/plan_to_orca.py plan.json --dry-run`.
4. Execute with `ai-pipeline-orca` (create → start-ready → check --wait → mark done → repeat).
5. G3 info may be collected together with G1 when the user already answers it (record it in `decisions.md`, put `G3` in `done.json`); the plan must still list the G3 gate so validation passes and the skip is explicit. At every gate: stop starting dependents, ask the human, record the answer in `decisions.md`, add the gate id to `done.json`.

## Hard rules
- Data first; never promise production accuracy from synthetic data alone.
- Version every dataset/model; after every evaluation produce `report.md` + `report.html` (`ai-pipeline-report`).
- User-facing text in Vietnamese.
- Know the ceiling before burning rounds (`ai-pipeline-feasibility`).
- Never skip G1/G2/G3; never train without G3 information.
