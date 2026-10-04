---
name: ai-pipeline-agents
description: Agent roster gate for the AI pipeline - before any parallel worker is started, use the Orca runtime to check which agent runtimes are available, let the human choose one orchestrator and the worker agents for code execution and for the model debate, and record it in runs/<id>/agents.json.
---

# Choose the agents before launching workers

**Rule:** the coordinator never starts a parallel worker before the human has chosen who works. `scripts/plan_to_orca.py --create/--start-ready` refuses to run without `runs/<id>/agents.json` and only uses agents selected there.

## Steps
1. **Check availability through Orca** (read-only, never installs/logs in/launches a session):
   `python scripts/agent_roster.py detect` — the Orca CLI has no "list agents" command, but Orca itself knows a catalog of ~40 agents (id + launch command, e.g. `command-code`, `pi`, `kimi`, `antigravity`→`agy`, `opencode2`). The script reads that catalog from the installed Orca bundle (fallback: built-in list), probes each launch command on PATH, and checks `orca status/account/host`. Use Orca's ids exactly (a wrong id such as `commandcode` makes `worker-start` fail). `--all` also lists catalog agents that are not installed (the human may ask to install one: that is a package install ⇒ ask first). If the runtime is not reachable: `orca open --json` first.
   **Readiness is not verified by PATH:** before putting an agent in the roster run `python scripts/agent_roster.py probe <id>` (a no-op worker through Orca; rebinds the coordinator afterwards). An agent that does not report `worker_done` (e.g. stuck on its own first-run trust/permission prompt, login, or model setup) must not be selected until the human fixes it and the probe passes.
2. **Ask the human** (Claude Code: AskUserQuestion; Codex: in conversation) with the *detected usable* list only:
   - **Orchestrator** (1 agent): the session that runs the workflow. It cannot be swapped mid-session: if the human picks another agent than the current session, tell them to restart the workflow from that agent and stop.
   - **Code workers** (≥1): execute code — module-dev, integrator, error-analyst, feasibility-analyst.
   - **Debate workers** (≥2 *different* agents when available): model-proposer, critic, architect/judge — different agents give independent proposals.
   - Optional: analysis workers (data-analyst, requirements-analyst, researcher); default = code workers.
3. **Record:** `python scripts/agent_roster.py select runs/<id> --orchestrator <id> --code a,b --debate a,c [--analysis ...]` — validates against detection and writes `agents.json`. Put the choice in `decisions.md` and the notebook (`decision`).
4. **Use:** tasks in `plan.json` may leave `agent` empty or `"auto"` (round-robin inside the role's group) or name an agent that is in the roster; naming an unselected agent aborts the start. Changing the roster later = ask the human again and re-run `select`.
5. Re-run `detect` at the start of every new session/resume: an agent that disappeared or an Orca runtime that stopped makes the stored roster stale.

`scripts/project_status.py` reports whether `agents.json` exists and what it contains.
