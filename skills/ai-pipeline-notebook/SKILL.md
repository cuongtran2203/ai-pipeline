---
name: ai-pipeline-notebook
description: Per-problem lab notebook for the AI pipeline - log every experiment, decision and lesson learned into runs/<id>/notebook, and (optionally) use Google NotebookLM via the notebooklm skill for grounded research and as a living knowledge base. Use at run start, after every experiment/gate/phase, and during research.
---

# Notebook per problem (sổ thí nghiệm + đúc kết)

One notebook per run: `runs/<id>/notebook/`. It records **what we tried, what happened, what we learned**, so later rounds do not repeat dead ends and the human can read the story. Tool: `python scripts/notebook.py` (stdlib, works in Claude Code and Codex).

## Local notebook (always on, automatic)
- Start of run: `python scripts/notebook.py init runs/<id> --title "<problem>"`.
- Log with `python scripts/notebook.py log runs/<id> --type <t> --title "..." --body "..." [--metrics '{"val_acc":0.9866}'] [--refs path,path] [--tags a,b] --author <role>`.

| type | when | body must contain |
|---|---|---|
| `experiment` | every train/eval/probe/ablation (workers, right after the result) | hypothesis · setup (data version, model version, seed, key config) · result (metrics) · conclusion · next step |
| `decision` | each gate (G1/G2/G3), model choice, retarget, stop | options · choice · why · who decided |
| `insight` | the **đúc kết**: something now known that was not before | the claim · evidence (metric/file) · confidence (confirmed / hypothesis) · implication |
| `research` | any finding from papers/NotebookLM/web | question · answer · source (URL / notebook source name) · how verified |
| `error` | an error cluster or failure analysis | cluster · count · confirmed cause or hypothesis · fix idea |
| `gate` | human gate asked/answered | question · answer |

Rules: log failures and negative results too; numbers with their evaluation set; separate confirmed vs hypothesis; ≤ 10 lines per entry; never paste customer data or secrets (the notebook may be exported). Workers log their own experiments; the **coordinator writes 3–5 `insight` entries at the end of every phase** (what we learned, evidence, implication for the next phase) and one `decision` per gate. `insights.md` is rebuilt from insight/decision entries automatically.

Before planning a new round or hypothesis: `python scripts/notebook.py show runs/<id> --type experiment` — do not repeat an experiment that is already logged.

## NotebookLM (optional, grounded research + knowledge base)
Uses the third-party skill https://github.com/PleasePrompto/notebooklm-skill. Facts about it (from its README): it **queries existing notebooks only** via browser automation of a logged-in Google account; it cannot create notebooks or add sources; each question opens a fresh browser (stateless); free-tier rate limits; local Claude Code oriented; notebooks must be shared by link; its author warns Google may flag automation and recommends a **dedicated Google account**. Therefore:
- **Human steps** (cannot be automated): create one NotebookLM notebook per problem; upload sources (papers, docs, the exported notebook); share the link; install/authenticate the skill with a dedicated account. Record the URL in `notebook/notebooklm.json` (`notebook_url`).
- **Only upload non-confidential material.** A link-shared notebook is readable by anyone with the link: no customer data, no secrets, no raw datasets.
- **Research (A3 researcher, F0/B0 feasibility analyst, proposers):** if the `notebooklm` skill is installed and `notebook_url` is set, ask it questions first (grounded answers with citations from the uploaded sources), then log each answer as `research` with the source and how it was cross-checked. If it is not available, fall back to normal web research and say so in the entry. Treat NotebookLM answers as source-grounded, not as ground truth; verify numbers that drive decisions.
- **Update (keep the notebook alive):** at the end of each phase and each optimize round run `python scripts/notebook.py export runs/<id>`; it writes `notebook/export/notebook-<time>.md`. Ask the human to upload it to the NotebookLM notebook, replacing the previous export, and tick it in `sources_uploaded`. Later research questions then also draw on our own experiment history.
- Never run the NotebookLM auth/login yourself on the user's behalf and never store Google credentials in the repo; the skill keeps its own browser state outside this project.

## Where it plugs in
`ai-pipeline` (init at start; insights per phase), `ai-pipeline-analysis` (research via NotebookLM), `ai-pipeline-module-dev` (log each experiment), `ai-pipeline-feasibility` (log each ceiling checkpoint and round), `ai-pipeline-integration` (error clusters), `ai-pipeline-status` (reports notebook size/export freshness).
