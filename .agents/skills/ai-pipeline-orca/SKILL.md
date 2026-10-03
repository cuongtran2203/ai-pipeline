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
