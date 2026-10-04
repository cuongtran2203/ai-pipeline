# AI Pipeline Workflow — agent instructions (Claude Code + Codex)

This repo is a multi-agent workflow: **spec file in → plan + parallel execution (Orca) out**, for projects that build AI pipelines (CV/NLP). Source: `AI_workflow_proposal.md`.

## Start
User says "run ai-pipeline on <spec>" → use skill `ai-pipeline` (`skills/ai-pipeline/SKILL.md`; mirrored in `.claude/skills` and `.agents/skills`). Edit only `skills/`, then `python scripts/sync_skills.py`.

## Sổ thí nghiệm (mỗi bài toán một notebook)
Ghi mọi thí nghiệm, quyết định, đúc kết vào `runs/<id>/notebook/` bằng `scripts/notebook.py` (skill `ai-pipeline-notebook`), kể cả kết quả âm; đọc sổ trước khi đề xuất vòng mới. NotebookLM (skill `notebooklm` của bên thứ ba) là tuỳ chọn cho research và kho tri thức: người tự tạo notebook/upload nguồn, chỉ upload nội dung không bí mật, không tự đăng nhập Google thay người dùng.

## Biết giới hạn trước khi lặp
Trước khi chạy vòng tối ưu, ước lượng ceiling (skill `ai-pipeline-feasibility`): sàn lỗi do nhãn nhiễu/mơ hồ, prior art, learning curve từ baseline rẻ, sai số chuẩn bộ đánh giá. Mục tiêu > ceiling → hỏi người, không chạy lặp. Mỗi vòng phải có giả thuyết + mức cải thiện dự đoán; dừng khi hết hiệu quả cận biên.

## Hiện trạng
Tiếp tục một run hoặc user hỏi "đang ở bước nào / làm gì tiếp" → dùng skill `ai-pipeline-status` (cử 1 agent `status-assessor` qua Orca, ghi `runs/<id>/STATUS.md`) trước khi chạy bất kỳ phase nào.

## Layout
- `skills/` workflow skills (source of truth) · `roles/` worker role prompts · `schemas/` plan schema
- `templates/` spec/report templates · `scripts/` validate_spec, plan_to_orca, render_report, sync_skills
- `examples/` sample spec, plan, eval · `runs/<run_id>/` per-run state (gitignored)

## Đừng làm phức tạp hoá vấn đề
Hầu hết vấn đề khi làm model/pipeline AI quy về 2 câu hỏi. Trả lời chúng trước khi nghĩ đến kỹ thuật cao siêu:
1. **Đã phân tích kỹ dataset chưa?** Phân bố tập test có khác tập train quá nhiều không (nguồn, chất lượng ảnh/văn bản, lớp, độ khó)? Lệch phân bố là nguyên nhân số 1 khiến kết quả test tốt mà prod kém.
2. **Đã thực sự hiểu model đã chọn chưa?** Xem lại cách training (data, augmentation, loss, cấu hình) và đọc kỹ lỗi sai của model trên tập test; giải pháp phải xuất phát từ các lỗi đó.
Ưu tiên sửa data / tiền-hậu xử lý / cách train trước khi đổi sang model hay kiến trúc phức tạp hơn. Giải pháp đơn giản nhất giải thích được lỗi thì chọn nó.

## Roles
- **Coordinator** (you, when the user starts a run): validates spec, runs gates with the human, drives Orca. Never does worker tasks inline.
- **Worker** (live Orca preamble with Task/Dispatch IDs present): follow the preamble and your role file `roles/<role>.md`; stay in your owned paths; finish with `worker_done`.

## Non-negotiables
1. Orca (`orca orchestration …`) provides all multi-agent coordination; load its guide with `orca skills get orchestration`. No non-Orca subagents for pipeline workers.
2. Human gates G1 (clarify spec), G2 (approve plan + deployment), G3 (GPU server info before training) are mandatory.
3. Data first. Version every dataset and model. Real labeled eval set ≥50 samples when real data exists; synthetic-only results never claim production accuracy.
4. After every experiment round (any train/eval/probe/ablation/optimize round) ALWAYS write `report.md` (concise: how the experiment ran, which problem it solved, detailed results) and `report.html` (visualize error clusters) into `runs/<id>/reports/round-NN-<slug>/`; no next round before both exist. Format: write `report.md` (Vietnamese; Tổng quan / Nội dung chi tiết / Kết luận) and `report.html` (clustered errors) via `scripts/render_report.py`.
5. All user-facing text in Vietnamese.
6. Parallel development tasks each get their own git worktree/branch (`worktree: new-child`); never let two workers edit the same checkout. Share artifacts/reports via the absolute run dir; integrator merges branches.
7. **Sandbox** (`ai-pipeline-sandbox`): mọi việc triển khai/kiểm thử/benchmark/huấn luyện chạy trong 1 sandbox container trên server (hoặc local nếu human cho phép rõ); không tự ý cài thư viện, cần gói nào thì `ask` trước và chỉ cài trong container.
