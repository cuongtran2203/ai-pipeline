---
name: ai-pipeline-analysis
description: Phase 1 of the AI pipeline workflow. Launch parallel analysis workers (data-analyst, requirements-analyst, researcher) via Orca and collect their artifacts.
---

# Analysis (parallel wave)

Tasks (all depend only on G1): `A1` data-analyst, `A2` requirements-analyst, `A3` researcher — prompts in `roles/`. Prefer different agents (e.g. A1 claude, A2 codex, A3 claude).

Coordinator: start all three before waiting (`ai-pipeline-orca`). When each `worker_done` arrives, verify the artifact exists and meets its acceptance line. A worker failure caused by missing information → resolve with the human and retry only that task.

Researcher/feasibility tasks use NotebookLM when set up (see `ai-pipeline-notebook`) and log findings as `research`.

After A1–A3, run **F0** (`feasibility-analyst`, checkpoint C0, no training): estimates the achievable band before the debate. If `target > upper`, surface it at G2. See `ai-pipeline-feasibility`.

The data-analyst must deliver a concrete "Kế hoạch sinh dữ liệu" when data is scarce (see `roles/data-analyst.md`); if it is missing, send the task back.

Data-analyst findings feed everything downstream: if dataset quality is poor, tell the human now and adjust targets rather than proceeding silently.
