---
name: ai-pipeline-orca
description: How the coordinator runs the AI pipeline plan on Orca - parallel worker waves, gates, check/wait loop, release - for both Claude Code and Codex coordinators.
---

# Orca runtime for the pipeline

Resolve the CLI once: `ORCA_CLI_COMMAND` env if set, else `orca-dev` if `ORCA_DEV_REPO_ROOT`, else `orca-ide` on Linux (`orca` there collides with the GNOME screen reader), else `orca`. `scripts/plan_to_orca.py` applies the same rule and aborts before any mutation if the executable is missing. Then load the exact guide: `orca skills get orchestration` (and `--reference references/<file>.md` at action gates). If a command fails, report the exact error; do not switch executables.

Never use a non-Orca subagent tool for pipeline workers.

## Loop
```
python scripts/plan_to_orca.py plan.json --run-dir runs/<id> --create        # run-create + task-create (deps mapped)
python scripts/plan_to_orca.py plan.json --run-dir runs/<id> --start-ready   # worker-start every ready task
orca orchestration check --wait --types worker_done,escalation,question --timeout-ms 900000 --json
```
For each delivery: `question` → answer (or ask the human for gate-type questions) with `orca orchestration reply --id <message_id> --body "..."`; `worker_done` → verify the declared outcome and artifact against the task's acceptance line. Only when the outcome is `succeeded` AND the artifact passes the acceptance check, append the task id to `runs/<id>/done.json`. On `failed` (or artifact failing acceptance): keep the id out of `done.json` so dependents stay blocked, then retry or escalate before anything downstream starts. `worker-release --dispatch <id>` (or reuse/retain), ack, then `--start-ready` again. Repeat until every task is settled.

Rules from the Orca guide that matter here: a timeout/empty wait is a checkpoint, not failure; never stop/abandon/retry without positive proof of exit (see `references/recovery-and-cleanup.md`); do not end the turn while `worker-list --terminal-state reclaimable` returns workers.

## Practical notes (from real runs)
- Orca auto-releases a worker after `worker_done`; still confirm with `worker-list --terminal-state reclaimable` that nothing is left before ending the turn.
- Map a delivery back to a plan id through `task_map.json` (payload `taskId`), not through dispatch ids; `started.json` stores the real `dispatchId` per task.
- `--start-ready` skips gates already in `done.json`. `phase: train|probe` needs a dependency path to G2 and G3; `phase: build` to G2.
- A probe/train/build worker runs in its own worktree; the integrator merges its branch into master; remove merged worktrees afterwards with `orca worktree rm --worktree branch:<name>`.
- A worker agent not known to Orca (e.g. commandcode) cannot be supervised: use a recognised `--agent`, or drive its terminal manually and confirm by output files.

## Review checklist (mandatory before merging/committing any worker output)
Lessons from the v2 waves (RV3 found defects that the workers' own tests and a first coordinator pass missed). For every worker branch or owned-file change:
1. **Scope:** `git diff --stat` touches only Ownership (+ allowed derived mirrors); no secrets/binaries/data; nothing outside the task.
2. **Run it yourself:** the full suite (`python -m unittest discover -s tests`), plus the backward-compat commands (old plan `--dry-run`, `project_status`, `render_report`, example plans).
3. **Bite check:** copy the worker's new tests onto the code *before* the change (or revert the core fix on a temp copy): they must fail. Tests that pass either way prove nothing.
4. **Every writer, not just the changed one:** `grep` the whole repo for direct writes to the shared files the change is about (state JSON/JSONL, KG, audit); an API fix is not closed while another script still bypasses it.
5. **Failure paths:** fault after the Orca receipt but before the state write, worker-start failure (rollback), missing receipt, corrupt vs missing vs empty state file (must not be overwritten), two coordinators/processes at once (stress with real processes), Windows paths/BOM.
6. **Live state:** run migrations and new logic on a *copy* of the real run's state before merging.
7. **Verdict wording:** CLOSED / PARTIAL / OPEN per original finding, with the probe that shows it; record the review in the notebook (`decision`).
Ask a second model (e.g. codex `gpt-6-sol` via a read-only critic task) to attack the result when the change touches gates, caps, state files or the test seal.

## Close and delete reviewed branches
After a worker is settled (`worker_done` succeeded, `task_id` in `done.json`) the orchestrator **reviews** it: acceptance line met, `git diff master...<branch> --stat` shows only owned paths, no secrets/binaries/data/checkpoints, tests/parity evidence present. If the review finds nothing wrong, **close and delete the branch**: `python scripts/branch_cleanup.py runs/<id>` (dry run) then `... --apply --reviewed <TASK_IDS>`. The script merges new tracked paths (`--no-ff`), restores ignored files into the run dir, tags unmerged tips as `archive/<branch>`, then `orca worktree rm` (no `--force`) and deletes the branch; it refuses on a dirty worktree, a task not in `done.json`, or a dirty master. If the review finds a problem: keep the branch, send the worker feedback or retry; never delete it.

## Gates
`kind: "gate"` tasks are never workers. When `--start-ready` prints `GATE <id> ready`: ask the human, write the answer to `decisions.md`, add the id to `done.json`. Workers that need a human decision mid-task use the `ask` command from their preamble; relay to the human and `reply`.

## Per-runtime notes
| | Claude Code coordinator | Codex coordinator |
|---|---|---|
| Ask human | `AskUserQuestion` | ask in the conversation, wait for the reply |
| Skills dir | `.claude/skills` | `.agents/skills` |
| Instructions | `CLAUDE.md` → `@AGENTS.md` | `AGENTS.md` |
| Workers | any task may set `"agent": "claude"` or `"codex"` in plan.json | same |

Run `python scripts/sync_skills.py` after editing `skills/`. Worker prompts are runtime-neutral (`roles/*.md`); workers receive them by path inside the task spec.

## Parallel safety
Every parallel build/train task runs in its **own git worktree + branch** (`plan_to_orca.py` defaults `worktree` to `new-child`, named `<run_id>-<task_id>`, for module-dev/integrator/error-analyst and `phase: train|build`; read-only analysis/planning tasks use `current`). Requirements:
- The project must be a git repo with ≥1 commit (`git init && git add -A && git commit -m init`); the script aborts before mutating Orca otherwise.
- Artifacts, `eval.json` and reports go to the **absolute** run dir (shared); code stays on the worker's branch and is committed there.
- Disjoint `owns` paths still apply; the integrator merges task branches (resolving conflicts) before e2e eval. Remove merged worktrees after release.
- Override per task with `"worktree": "current"` only for non-conflicting, non-code work. Prefer wide waves over chains deeper than 3–4.
