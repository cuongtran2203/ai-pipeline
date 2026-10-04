---
name: ai-pipeline-graph
description: When ai-pipeline is initialised in a project, ALWAYS build a knowledge graph of the whole project with Graphify (graphifyy on PyPI, command graphify), refresh it after each phase, and let agents query it before grepping. Includes the approval, privacy and exclusion rules for installing and running it.
---

# Project knowledge graph with Graphify

Standing rule from the user: **whenever ai-pipeline is initialised in a project, graph the whole project into a knowledge graph with Graphify** (https://github.com/Graphify-Labs/graphify). It gives every agent a map of code, docs, schemas and configs so they locate things by relationship instead of blind grep/reads.

Facts (from the Graphify README): PyPI package **`graphifyy`** (double *y*), command `graphify`, Python ≥ 3.10, Apache-2.0/MIT. Code is parsed **locally** with tree-sitter (`--code-only`: no API, nothing leaves the machine). Docs/PDF/images need an **LLM backend** (claude/openai/gemini/deepseek/ollama/bedrock/azure) = content is sent to that provider. Outputs in `graphify-out/`: `graph.json`, `graph.html`, `GRAPH_REPORT.md`. Query: `graphify query "…"`, `graphify path A B`, `graphify explain X`.

## Rules before running anything (sandbox rule applies)
1. **Installing is a package install ⇒ ask the human first** (`ai-pipeline-sandbox`): name `graphifyy` (check the spelling against the README to avoid a look-alike package), version pinned, extras only if approved (`[pdf]`, `[anthropic]`…), isolated environment (`uv tool`/`pipx`/venv or a container), never the system Python. Record the approval, location and version in `decisions.md`.
2. **Where it runs** follows the sandbox rule: in a container on the server, or locally only if the human allows it explicitly. Record in `decisions.md`.
3. **Privacy**: default to `--code-only` (local AST). Docs/markdown/PDF extraction only with an approved backend (prefer a local one such as ollama when documents are sensitive) and only on the paths approved for it. Say plainly what leaves the machine.
4. **Exclude** from the graph: datasets/images, customer data, `runs/*/data`, model checkpoints, `.git`, `.venv`, `node_modules`, `graphify-out/`, secrets (`.env`, keys, tokens). Verify how Graphify excludes with `graphify --help` after install (do not guess flags); if it has no ignore option, run it per included sub-folder.
5. Do **not** run `graphify claude|codex|cursor|gemini install` or `graphify install` (they edit CLAUDE.md/AGENTS.md/hooks/skills) without the human's explicit approval.
6. `graphify-out/` is derived: keep it out of git (`.gitignore`).

## Steps
1. **Init (always, first run in a project):** check `graphify --version`. Missing ⇒ list the package for approval (rule 1). Approved ⇒ install into the approved environment, then confirm commands with `graphify --help`.
2. **Build:** `graphify extract <included paths> --code-only` for code; add `--backend <approved>` only for approved doc paths. Whole project = every folder not excluded in rule 4. Run it inside the approved sandbox.
3. **Read the result:** open `graphify-out/GRAPH_REPORT.md` (god nodes, surprising connections, suggested questions) and log 2–4 `insight` entries to the notebook (`scripts/notebook.py`) with the file path as evidence.
4. **Use:** before grepping or reading many files, ask `graphify query "<question>"`, `graphify path "<A>" "<B>"`, `graphify explain "<symbol or doc>"`; cite the nodes/files found. If `graphify-out/graph.json` is absent or stale (older than the last phase), refresh it first.
5. **Refresh:** after every phase and after a large merge, update incrementally (Graphify's documented `--update` mode, re-checking the exact form with `--help`) and re-log new insights.
6. **If it cannot be installed/run** (no approval, no sandbox, backend refused): say so in `decisions.md` and continue without it; never install "temporarily".

`scripts/project_status.py` reports whether `graphify-out/graph.json` exists and how old it is.
