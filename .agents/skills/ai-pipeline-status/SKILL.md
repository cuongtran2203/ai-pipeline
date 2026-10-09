---
name: ai-pipeline-status
description: Determine where an AI-pipeline project stands and which step to run next. Use when resuming a run, when the user asks "dự án đang ở bước nào / cần làm gì tiếp", or at the start of ai-pipeline on a folder that already has runs/. Dispatches one read-only status-assessor agent via Herdr.
---

# Project status → next step

Use whenever the project already has state (`runs/<run_id>/`) or the user asks for the current standing. Never restart a phase before this check.

## Steps (coordinator)
1. Pick the run: the one the user names, else newest under `runs/`. No run → say so and start `ai-pipeline` at intake.
2. Quick look (cheap, deterministic): `python scripts/project_status.py runs/<id>` — phase, modules, gates, next actions.
3. Dispatch **one** assessor (read-only, no worktree needed) through Herdr; load `ai-pipeline-herdr` first:
   ```
   python scripts/herdr_rt.py run-create --run-dir <abs run dir> --objective "Status <run_id>"
   python scripts/herdr_rt.py task-create --run-dir <abs run dir> --run <RUN_ID> --task-title "Assess status" --spec "TASK ST1: Read roles/status-assessor.md and follow it for run dir <abs run dir>. Ownership: only <abs run dir>/STATUS.md. Acceptance: STATUS.md names the phase, blocked gates, running tasks and ordered next actions with evidence paths."
   python scripts/herdr_rt.py worker-start --run-dir <abs run dir> --run <RUN_ID> --task <TASK_ID> --worktree current --agent claude
   python scripts/herdr_rt.py check --run-dir <abs run dir> --wait --types worker_done,escalation,question --timeout-ms 600000
   ```
   Agent may be `codex` instead; the role file is runtime-neutral. After `worker_done` (verify `outcome: succeeded` and `STATUS.md` exists) → `worker-release`.
   If Herdr is unavailable, the coordinator performs the role steps itself and says so.
4. Report to the human in Vietnamese: phase, what is done/running/blocked, ordered next actions. **Do not execute** the next steps until the human confirms (blocked gates need their answer anyway).
5. On confirmation continue with the matching phase skill: intake → `ai-pipeline-intake`, analysis → `ai-pipeline-analysis`, planning → `ai-pipeline-planning`, build → `ai-pipeline-module-dev`, integration/optimize/release → `ai-pipeline-integration`. Reuse the existing Herdr Run (`_run` in `task_map.json`) and `done.json`; never recreate finished tasks.

## Phase detection (what `project_status.py` checks)
| Phase | Done when |
|---|---|
| 0 intake | `spec.md` passes `validate_spec` and `G1` in `done.json` |
| 1 analysis | `data_analysis.md`, `requirements.md`, `research.md` exist |
| 2 planning | `proposal.md`, `critique.md`, `architecture.md`, build `plan.json` exist and `G2` done |
| 3 build | every planned module (role module-dev hoặc task có execution mode) has `eval.json` + `report.md` + `report.html` (cần `G3` trước khi train; plan không train thì bỏ qua G3) |
| 4 integration | e2e `eval.json` + both reports |
| 5 optimize | targets met, else ≤3 rounds (`artifacts/opt-*`) |
| 6 release | `release/` exists |

`ceiling.json` is also read: `upper < target` without a recorded decision ⇒ blocked on the human.

The assessor also reports notebook state (`notebook/journal.jsonl` entry count, last `export`, whether `notebook_url` is set) and whether insights were written for the finished phases.

The script also reports the project knowledge graph (`graphify-out/graph.json` present? age) and advises building it (`ai-pipeline-graph`) when missing.

The script reads files only; the assessor adds Herdr runtime state (running/failed tasks) and artifact quality, and its judgment wins on conflict.
