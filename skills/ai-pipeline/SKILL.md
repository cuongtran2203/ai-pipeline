---
name: ai-pipeline
description: Entry point for the AI-pipeline workflow. Use when the user gives a spec file describing an AI system to build (CV/NLP) and wants a plan plus multi-agent execution coordinated by Orca. Works in Claude Code and Codex.
---

# ai-pipeline — spec → plan → multi-agent execution

Input: a spec file path (see `templates/spec.template.md`). You are the **coordinator**. Workers are Orca-managed agents (claude or codex). Load `ai-pipeline-orca` before launching anything.

**Resuming / "dự án đang ở đâu?"**: if `runs/` already has a run (or the user asks for status), run `ai-pipeline-status` FIRST; it dispatches one assessor agent and tells which phase to execute next. Only then continue.

## Agent roster (before ANY parallel worker)
Use the Orca runtime to check which agent runtimes are available (`python scripts/agent_roster.py detect`), let the human choose **one orchestrator** and the **worker agents** (code execution; debate — use different agents), record it with `agent_roster.py select` (`ai-pipeline-agents`). `plan_to_orca.py` refuses to create/start workers without `runs/<id>/agents.json`.

## Knowledge graph (always at init)
When ai-pipeline is initialised in a project, ALWAYS build the whole-project knowledge graph with Graphify first (`ai-pipeline-graph`: package/location/backend approvals, exclusions, `--code-only` by default) and refresh it after each phase; agents query it (`graphify query|path|explain`) before grepping.

## Notebook
At run start `python scripts/notebook.py init runs/<id> --title "<problem>"`. Workers log experiments; you log a `decision` per gate and 3–5 `insight` entries at the end of every phase; after each phase `notebook.py export` and ask the human to refresh the NotebookLM source (optional). See `ai-pipeline-notebook`.

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
- **After every experiment round** (a train/eval/probe/ablation round, an optimize round, a phase-end evaluation) ALWAYS produce `report.md` + `report.html` into `runs/<id>/reports/round-NN-<slug>/` (module-level: also into the module dir) via `ai-pipeline-report`. The md is concise and covers: how the experiment was run, which problem it solved (and which it did not), and the detailed results. The html visualizes the error clusters. A round is not finished, and the next round may not start, until both files exist and are logged in the notebook.
- After the orchestrator reviews a finished worker branch and finds no problem, close and delete it (`scripts/branch_cleanup.py`, `ai-pipeline-orca`); never delete unreviewed or unmerged work without the archive tag.
- Version every dataset/model.
- User-facing text in Vietnamese.
- Know the ceiling before burning rounds (`ai-pipeline-feasibility`).
- Never skip G1/G2/G3; never train without G3 information.
- Sandbox (`ai-pipeline-sandbox`): all execution/testing in a container on the server (local only if the human allows); never install libraries without permission. At G2/G3 ask: container/server, local allowed?, which libraries are allowed.
