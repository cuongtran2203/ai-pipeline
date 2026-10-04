---
name: ai-pipeline-agents
description: Agent roster gate for the AI pipeline - before any parallel worker is started, use the Orca runtime to check which agent runtimes are available, let the human choose one orchestrator and the worker agents for code execution and for the model debate, and record it in runs/<id>/agents.json.
---

# Choose the agents before launching workers

**Rule:** the coordinator never starts a parallel worker before the human has chosen who works. `scripts/plan_to_orca.py --create/--start-ready` refuses to run without `runs/<id>/agents.json` and only uses agents selected there.

## Steps
1. **Check availability through Orca** (read-only, never installs/logs in/launches a session):
   `python scripts/agent_roster.py detect` — it reads the Orca runtime (`orca status`, `account list`, `host list`) and probes the CLI of each agent id Orca can supervise (`claude, codex, opencode, opencode2, cursor, antigravity, muse, zcode`); agents Orca cannot supervise (e.g. `commandcode`) are shown as *unsupervised* and are not offered for workers (no `worker_done`, no lifecycle). If the runtime is not reachable: `orca open --json` first. Login state/credits cannot be verified without a session: an agent that fails to start is reported by Orca at `worker-start`.
2. **Ask the human** (Claude Code: AskUserQuestion; Codex: in conversation) with the *detected usable* list only:
   - **Orchestrator** (1 agent): the session that runs the workflow. It cannot be swapped mid-session: if the human picks another agent than the current session, tell them to restart the workflow from that agent and stop.
   - **Code workers** (≥1): execute code — module-dev, integrator, error-analyst, feasibility-analyst.
   - **Debate workers** (≥2 *different* agents when available): model-proposer, critic, architect/judge — different agents give independent proposals.
   - Optional: analysis workers (data-analyst, requirements-analyst, researcher); default = code workers.
3. **Record:** `python scripts/agent_roster.py select runs/<id> --orchestrator <id> --code a,b --debate a,c [--analysis ...]` — validates against detection and writes `agents.json`. Put the choice in `decisions.md` and the notebook (`decision`).
4. **Use:** tasks in `plan.json` may leave `agent` empty or `"auto"` (round-robin inside the role's group) or name an agent that is in the roster; naming an unselected agent aborts the start. Changing the roster later = ask the human again and re-run `select`.
5. Re-run `detect` at the start of every new session/resume: an agent that disappeared or an Orca runtime that stopped makes the stored roster stale.

`scripts/project_status.py` reports whether `agents.json` exists and what it contains.
