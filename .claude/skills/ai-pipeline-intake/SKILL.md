---
name: ai-pipeline-intake
description: Phase 0 of the AI pipeline workflow. Validate the spec file and run human Gate G1 to clarify platform, input, output, dataset and targets before planning.
---

# Intake (Gate G1)

1. `python scripts/validate_spec.py <spec> --json`. Each `questions[]` entry is a gap.
2. Always also probe, even if the spec looks complete:
   - **Platform**: GPU, CPU or edge/mobile? (drives model choice)
   - **Input**: what is given; what must be added to make output unambiguous? Each added element may itself be an AI module.
   - **Output**: what exactly; can outside factors change it (common in CV)?
   - **Dataset**: real data? count, labeled?, a few samples?, or description only (then synthetic tooling is needed — warn it proves pattern recognition, not prod accuracy). If real data exists, a labeled eval set of ≥50 samples is mandatory.
   - **Targets**: accuracy, speed, cost.
3. Ask the human (Claude Code: AskUserQuestion; Codex: ask in conversation). Batch questions, max 4 per round.
4. Write answers into the run's `spec.md` under the matching headings and `decisions.md`; re-run validate until `ok: true`.
5. Mark `G1` done in `done.json`.
