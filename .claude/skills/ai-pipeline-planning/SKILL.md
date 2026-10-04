---
name: ai-pipeline-planning
description: Phase 2 of the AI pipeline workflow. Multi-agent debate on model selection, then architect/judge produces architecture.md and the build-phase plan.json; human Gate G2 approves.
---

# Planning (debate → plan → Gate G2)

## Debate protocol
1. **Proposers** `P1` (accuracy-first) and `P2` (speed/cost-first) run in parallel, on different agents (claude + codex) for diversity. Input: A1–A3 artifacts.
2. **Critic** `P3` reads both, independent of the proposers.
3. **Judge/architect** `P4` decides, records rejected options, decomposes the system into modules and writes `plan.json` (`schemas/plan.schema.json`).
   If the critic raised a high-severity issue the judge cannot settle with evidence, run one extra cheap verification task before finalizing (max 1 extra round).

## Plan requirements
- Inputs include F0 `ceiling.json` (C0 band). The plan must start build with a **B0 baseline probe** (`"phase": "probe"`, role feasibility-analyst) before any full train task; train tasks depend on it.
- If A1 reports scarce data (hundreds of samples, synthetic only) or B0/C1 diagnoses data-limited, the plan MUST contain a data-generation task (generator tool, train-fold-only sources, per-fold regeneration, ablation per component on val) before the final train rounds; deferring it needs a written reason accepted at G2.
- Mark `"phase": "train"` / `"build"` explicitly (validation is by field, not by task title).
- Each module: AI model vs classical algorithm, reasoning tied to the customer's resources and data.
- Include pre/post-processing derived from data analysis.
- Gates G2 and G3 present; every train task depends on G3.
- Modules own disjoint directories so build workers can run in parallel.
- Verify: `python scripts/plan_to_orca.py plan.json --dry-run` succeeds (no cycles).

## Gate G2
Present to the human: **target vs estimated ceiling band (C0)** — if target > upper, offer: lower the target / get more or cleaner data / change scope — then chosen model per module, deployment method options (ask — it depends on the customer), expected metrics/cost, risks. Record the decision in `decisions.md`, mark `G2` done.
