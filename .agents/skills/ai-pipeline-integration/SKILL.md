---
name: ai-pipeline-integration
description: Phases 4-6 of the AI pipeline workflow. Integrate modules, test end-to-end, cluster errors, run bounded optimization rounds, and release.
---

# Integration, optimization, release

1. `I1` integrator: assemble modules + pre/post-processing; e2e eval on real val; measure each module and each end-to-end field against baseline.
2. `I2` error-analyst: cluster errors, confirm causes, produce `eval.json` → reports.
3. **Optimize loop** (≤3 rounds by default; stop rules and the hypothesis/predicted-gain requirement come from `ai-pipeline-feasibility`, checkpoint C2): per top error cluster create a task (role module-dev or integrator) with hypothesis + measurement; run clusters in parallel when owned directories are disjoint; re-run I1/I2; stop when requirements.md targets are met or rounds are exhausted, then report honestly.
4. **Release**: package per the deployment method approved at G2 (only within the scope the user allowed); final `report.md`/`report.html` and a recommendation on adopting the new checkpoint/config.
