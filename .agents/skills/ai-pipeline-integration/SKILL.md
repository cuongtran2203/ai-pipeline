---
name: ai-pipeline-integration
description: Phases 4-6 of the AI pipeline workflow. Integrate modules, test end-to-end, cluster errors, run bounded optimization rounds, and release.
---

# Integration, optimization, release

All e2e runs, tests, benchmarks and packaging happen inside the sandbox container (`ai-pipeline-sandbox`); no library installs without the human's permission.

1. `I1` integrator: assemble modules + pre/post-processing; e2e eval on real val; measure each module and each end-to-end field against baseline.
2. `I2` error-analyst: cluster errors, confirm causes, produce `eval.json` → reports.
3. **Optimize loop (BAT BUOC sau baseline, khong phai tuy chon)** (`ai-pipeline-optimize`, checkpoint C2): coordinator chay `python scripts/optimize.py next <run_dir> [--apply]` sau moi vong cho toi khi no tra STOP (dat target → `I-final` test khoa mot lan qua seal roi release; ceiling thap hon target hoac OBJECTIVE/NOISE → hoi nguoi; het hieu qua can bien; het max_rounds; het budget). Thieu diagnosis → task `DIAG-<comp>` truoc; co diagnosis → task theo nhanh da duyet (`R<NN>-<comp>-<action>`, hypothesis + predicted_gain bat buoc). Moi vong ket bang evaluate (val/OOF) + report + `optimize.py record`. Lap va phan tich loi chi tren val/OOF; cam split test.
Once a task branch is reviewed with no problem, close and delete it with `scripts/branch_cleanup.py` (see `ai-pipeline-orca`); the integrator still merges branches that must be integrated before e2e. Every optimize round ends with `report.md` + `report.html` in `runs/<id>/reports/round-NN-<slug>/` (`ai-pipeline-report`) before the next round starts. Log each error cluster (`--type error`) and each optimize round outcome to the notebook.
4. **Release**: package per the deployment method approved at G2 (only within the scope the user allowed); final `report.md`/`report.html` and a recommendation on adopting the new checkpoint/config.
