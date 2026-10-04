# AI Pipeline Workflow

Spec file → multi-agent plan + execution, coordinated by Orca, runnable from **Claude Code** or **Codex**.

```
spec.md ─▶ G1 clarify ─▶ Analysis ×3 ─▶ Debate/Plan ─▶ G2 approve ─▶ G3 GPU info
        ─▶ Module build ×N (parallel) ─▶ Integration/Error analysis ─▶ Optimize ─▶ Release
```

## Use
1. Install/open Orca; check `orca status --json`.
2. `python scripts/sync_skills.py` (once, and after editing `skills/`).
3. Open this folder in Claude Code or Codex and say: `Chạy ai-pipeline với spec examples/sample_spec.md`.
4. Answer the three human gates (G1/G2/G3). Outputs land in `runs/<run_id>/`.

## Helpers
| Script | Purpose |
|---|---|
| `scripts/validate_spec.py <spec>` | list open questions for G1 |
| `scripts/plan_to_orca.py plan.json [--dry-run \| --create \| --start-ready]` | DAG → Orca tasks/workers |
| `scripts/render_report.py eval.json` | Vietnamese `report.md` + clustered-error `report.html` |
| `scripts/project_status.py [run_dir]` | phase hiện tại + việc cần làm tiếp (skill `ai-pipeline-status`) |
| `ceiling.json` (skill `ai-pipeline-feasibility`) | ước lượng giới hạn khả thi C0/C1/C2, luật dừng vòng tối ưu |
| `scripts/notebook.py init\|log\|export\|show` | sổ thí nghiệm theo bài toán + export cho NotebookLM (skill `ai-pipeline-notebook`) |
| Graphify (skill `ai-pipeline-graph`) | knowledge graph toàn dự án khi init; `graphify query/path/explain` |
| `scripts/obsidian_vault.py [--graphify EXE]` | vault Obsidian: docs + sổ thí nghiệm (wikilinks) + code graph Graphify |
| `scripts/agent_roster.py detect\|select\|show` | kiểm tra agent khả dụng qua Orca, người dùng chọn orchestrator/workers |
| `scripts/branch_cleanup.py <run_dir> [--apply --reviewed IDS]` | đóng và xóa nhánh worker đã review (merge/tag lưu trước khi xóa) |
| `scripts/sync_skills.py [--check]` | `skills/` → `.claude/skills`, `.agents/skills` |

See `AGENTS.md` for rules and `skills/ai-pipeline/SKILL.md` for the phases.
