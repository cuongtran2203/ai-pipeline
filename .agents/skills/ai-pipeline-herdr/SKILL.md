---
name: ai-pipeline-herdr
description: How the coordinator runs the AI pipeline plan on Herdr - parallel worker panes, gates, check/wait loop, release - for both Claude Code and Codex coordinators.
---

# Herdr runtime for the pipeline

[Herdr](https://github.com/herdrdev/herdr) is a terminal runtime for coding agents (workspace → tab → pane; each pane's agent is `working|blocked|idle|done`). It has no task DAG, run or `worker_done`, so those live as files in the run dir, driven by `scripts/herdr_rt.py` (backend) and `scripts/worker_done.py` (worker protocol). `scripts/plan_to_herdr.py` keeps the admission/cap/fencing logic unchanged and aborts before any mutation if the `herdr` executable is missing (`HERDR_CLI_COMMAND` overrides the name). Run `python scripts/herdr_rt.py verify` once per machine: it checks every herdr subcommand the backend uses against `--help`; the only place to fix syntax is `HERDR_CMDS` in `herdr_rt.py`. If a command fails, report the exact error; do not switch executables. Start `herdr` (session) before the first run.

Never use a non-Herdr subagent tool for pipeline workers.

## Loop
```
python scripts/plan_to_herdr.py plan.json --run-dir runs/<id> --create        # run-create + task-create (deps mapped) -> tasks/*.json
python scripts/plan_to_herdr.py plan.json --run-dir runs/<id> --start-ready   # worker-start: git worktree + new tab/pane + agent + prompt
python scripts/herdr_rt.py check --run-dir runs/<id> --wait --types worker_done,escalation,question --timeout-ms 900000
```
For each event: `question` (a worker ran `worker_done.py ... ask`; its pane is `blocked`) → answer yourself, or ask the human for gate-type questions, then `python scripts/worker_done.py runs/<id> answer --ask <ask_id> --text "..."` and `herdr pane send-text <pane> "<answer>"`; `worker_done` → verify the declared outcome and artifact against the task's acceptance line. Only when the outcome is `succeeded` AND the artifact passes the acceptance check, run `python scripts/settle_task.py runs/<id> <task_id>` (appends the plan id to `runs/<id>/done.json`). On `failed` (or artifact failing acceptance): keep the id out of `done.json` so dependents stay blocked, then retry or escalate before anything downstream starts. `python scripts/herdr_rt.py worker-release --run-dir runs/<id> --dispatch <id>` closes the pane, then `--start-ready` again. Repeat until every task is settled.

Rules that matter here: a timeout/empty wait is a checkpoint, not failure; never close/retry a pane without positive proof of exit (`herdr pane read <pane> --source recent`, `herdr agent get <pane>`); do not end the turn while `worker-list` shows unreleased panes.

## Practical notes
- Herdr does not auto-release: after `worker_done`, release the dispatch yourself and confirm with `herdr_rt.py worker-list` that nothing is left.
- Map an event back to a plan id through `task_map.json` (payload `task`), not through dispatch ids; `started.json` stores the real `dispatchId` per task. Each worker pane gets `HERDR_DISPATCH_ID` (recorded by `seal.py`).
- `--start-ready` skips gates already in `done.json`. `phase: train|probe` needs a dependency path to G2 and G3; `phase: build` to G2.
- A probe/train/build worker runs in its own worktree (`../.worktrees/<repo>/<run_id>-<task_id>`); the integrator merges its branch into master; remove merged worktrees afterwards with `scripts/branch_cleanup.py`.
- Agents are recognised by Herdr from the command in the pane (`claude`, `codex`, `pi`, `opencode`, …). One it cannot classify (e.g. command-code) → `scripts/run_headless_agent.py`, confirm by output files.
- 1.0.0.rc was probed against herdr 0.9.3 + claude (Windows); on a new machine watch `herdr_rt.py verify` and one `probe` (`agent_roster.py probe <id>`) before trusting a wave.

## Review checklist (mandatory before merging/committing any worker output)
Lessons from the v2 waves (RV3 found defects that the workers' own tests and a first coordinator pass missed). For every worker branch or owned-file change:
1. **Scope:** `git diff --stat` touches only Ownership (+ allowed derived mirrors); no secrets/binaries/data; nothing outside the task.
2. **Run it yourself:** the full suite (`python -m unittest discover -s tests`), plus the backward-compat commands (old plan `--dry-run`, `project_status`, `render_report`, example plans).
3. **Bite check:** copy the worker's new tests onto the code *before* the change (or revert the core fix on a temp copy): they must fail. Tests that pass either way prove nothing.
4. **Every writer, not just the changed one:** `grep` the whole repo for direct writes to the shared files the change is about (state JSON/JSONL, KG, audit); an API fix is not closed while another script still bypasses it.
5. **Failure paths:** fault after the Herdr receipt but before the state write, worker-start failure (rollback), missing receipt, corrupt vs missing vs empty state file (must not be overwritten), two coordinators/processes at once (stress with real processes), Windows paths/BOM.
6. **Live state:** run migrations and new logic on a *copy* of the real run's state before merging.
7. **Verdict wording:** CLOSED / PARTIAL / OPEN per original finding, with the probe that shows it; record the review in the notebook (`decision`).
Ask a second model (e.g. codex `gpt-6-sol` via a read-only critic task) to attack the result when the change touches gates, caps, state files or the test seal.

## Close and delete reviewed branches
After a worker is settled (`worker_done` succeeded, `task_id` in `done.json`) the orchestrator **reviews** it: acceptance line met, `git diff master...<branch> --stat` shows only owned paths, no secrets/binaries/data/checkpoints, tests/parity evidence present. If the review finds nothing wrong, **close and delete the branch**: `python scripts/branch_cleanup.py runs/<id>` (dry run) then `... --apply --reviewed <TASK_IDS>`. The script merges new tracked paths (`--no-ff`), restores ignored files into the run dir, tags unmerged tips as `archive/<branch>`, then `git worktree remove` (no `--force`) and deletes the branch; it refuses on a dirty worktree, a task not in `done.json`, or a dirty master. If the review finds a problem: keep the branch, send the worker feedback or retry; never delete it.

## Gates
`kind: "gate"` tasks are never workers. When `--start-ready` prints `GATE <id> ready`: ask the human, write the answer to `decisions.md`, add the id to `done.json`. Workers that need a human decision mid-task run `worker_done.py ... ask`; relay to the human and `answer`.

## Per-runtime notes
| | Claude Code coordinator | Codex coordinator |
|---|---|---|
| Ask human | `AskUserQuestion` | ask in the conversation, wait for the reply |
| Skills dir | `.claude/skills` | `.agents/skills` |
| Instructions | `CLAUDE.md` → `@AGENTS.md` | `AGENTS.md` |
| Workers | any task may set `"agent": "claude"` or `"codex"` in plan.json | same |

Run `python scripts/sync_skills.py` after editing `skills/`. Worker prompts are runtime-neutral (`roles/*.md`); workers receive them by path inside the task spec.

## Parallel safety
Every parallel build/train task runs in its **own git worktree + branch** (`plan_to_herdr.py` defaults `worktree` to `new-child`, named `<run_id>-<task_id>`, for module-dev/integrator/error-analyst and `phase: train|build`; read-only analysis/planning tasks use `current`). Requirements:
- The project must be a git repo with ≥1 commit (`git init && git add -A && git commit -m init`); the script aborts before mutating Herdr otherwise.
- Artifacts, `eval.json` and reports go to the **absolute** run dir (shared); code stays on the worker's branch and is committed there.
- Disjoint `owns` paths still apply; the integrator merges task branches (resolving conflicts) before e2e eval. Remove merged worktrees after release.
- Override per task with `"worktree": "current"` only for non-conflicting, non-code work. Prefer wide waves over chains deeper than 3–4.
