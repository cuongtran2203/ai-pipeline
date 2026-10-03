---
name: ai-pipeline-analysis
description: Phase 1 of the AI pipeline workflow. Launch parallel analysis workers (data-analyst, requirements-analyst, researcher) via Orca and collect their artifacts.
---

# Analysis (parallel wave)

Tasks (all depend only on G1): `A1` data-analyst, `A2` requirements-analyst, `A3` researcher — prompts in `roles/`. Prefer different agents (e.g. A1 claude, A2 codex, A3 claude).

Coordinator: start all three before waiting (`ai-pipeline-orca`). When each `worker_done` arrives, verify the artifact exists and meets its acceptance line. A worker failure caused by missing information → resolve with the human and retry only that task.

Data-analyst findings feed everything downstream: if dataset quality is poor, tell the human now and adjust targets rather than proceeding silently.
