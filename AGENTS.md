# AI Pipeline Workflow — agent instructions (Claude Code + Codex)

This repo is a multi-agent workflow: **spec file in → plan + parallel execution (Orca) out**, for projects that build AI pipelines (CV/NLP). Source: `AI_workflow_proposal.md`.

## Start
User says "run ai-pipeline on <spec>" → use skill `ai-pipeline` (`skills/ai-pipeline/SKILL.md`; mirrored in `.claude/skills` and `.agents/skills`). Edit only `skills/`, then `python scripts/sync_skills.py`.

## Thành phần yếu: chẩn đoán trước khi sửa
Khi lên plan, architect lên sẵn playbook xử lý cho cả pipeline (`playbook.json` + sơ đồ `scripts/playbook_diagram.py`, người dùng duyệt ở G2). Thành phần dưới mục tiêu → cử agent `weakness-diagnostician` (skill `ai-pipeline-diagnose`) xác minh bằng thí nghiệm phân biệt: DATA (ít dữ liệu thật / data sinh lệch phân bố), MODEL (thiếu năng lực: đối tượng nhỏ, độ phân giải…), AUX (cần model/module phụ trợ, vd. tách ô/ký tự của vùng date), hay NOISE (nhãn mơ hồ); rồi chọn nhánh hành động đã duyệt. Không sửa theo cảm tính.

## Đóng nhánh sau khi review
Khi một nhánh/worktree của worker đã done và orchestrator review (worker_done succeeded + acceptance + diff chỉ gồm file thuộc phạm vi, không có secrets/binary/dữ liệu) không thấy vấn đề → ĐÓNG và XÓA nhánh đó bằng `python scripts/branch_cleanup.py runs/<id> --apply --reviewed <TASK_IDS>` (xem dry-run trước). Không bao giờ xóa mất công việc: nhánh chưa merge thì merge `--no-ff` (new path) hoặc khôi phục file vào run dir; nhánh có commit chưa merge được lưu bằng tag `archive/<branch>` trước khi xóa; từ chối nếu task chưa trong done.json, worktree còn thay đổi chưa commit, hoặc master có thay đổi tracked chưa commit. Có vấn đề khi review → giữ nhánh, sửa/retry, không xóa.

## Chọn agent trước khi chạy song song
Trước khi dùng worker song song: dùng Orca runtime kiểm tra agent nào khả dụng (`python scripts/agent_roster.py detect`), cho người dùng chọn 1 orchestrator + các worker (code, debate — debate dùng ≥2 agent khác nhau), ghi `runs/<id>/agents.json` (`agent_roster.py select`, skill `ai-pipeline-agents`). `plan_to_orca.py` từ chối tạo/chạy worker khi thiếu `agents.json`. Agent Orca không giám sát được (vd. commandcode) không dùng làm worker.

## Knowledge graph dự án (Graphify)
Khi ai-pipeline được init vào một dự án, LUÔN dựng knowledge graph toàn dự án bằng Graphify (skill `ai-pipeline-graph`): cài/chạy theo quy tắc sandbox (hỏi trước khi cài `graphifyy`, ghi vào `decisions.md`), mặc định `--code-only` (không gửi gì ra ngoài), tài liệu chỉ qua backend được duyệt, loại trừ dữ liệu/ảnh/checkpoint/secrets. Cập nhật sau mỗi phase. Tài liệu/sổ thí nghiệm graph bằng Obsidian: `python scripts/obsidian_vault.py --graphify <venv>/graphify` sinh `vault/` (mở bằng Obsidian, xem Graph view). Cần định vị code/tài liệu thì `graphify query|path|explain` trước khi grep. Không chạy `graphify claude|codex install` khi chưa được duyệt.

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
4. After every experiment round (any train/eval/probe/ablation/optimize round) ALWAYS write `report.md` (concise: how the experiment ran, which problem it solved, detailed results) and `report.html` (visualize error clusters) into `runs/<id>/reports/round-NN-<slug>/`; no next round before both exist. Format: Markdown by default (docx only if the user asks), the fixed 3-part template in `templates/report.template.md` (Tổng quan / Nội dung chi tiết / Kết luận), no execution diary or long logs, N/A for unmeasured values. Write `report.md` (Vietnamese; Tổng quan / Nội dung chi tiết / Kết luận) and `report.html` (clustered errors) via `scripts/render_report.py`.
5. All user-facing text in Vietnamese.
6. Parallel development tasks each get their own git worktree/branch (`worktree: new-child`); never let two workers edit the same checkout. Share artifacts/reports via the absolute run dir; integrator merges branches.
7. **Sandbox** (`ai-pipeline-sandbox`): mọi việc triển khai/kiểm thử/benchmark/huấn luyện chạy trong 1 sandbox container trên server (hoặc local nếu human cho phép rõ); không tự ý cài thư viện, cần gói nào thì `ask` trước và chỉ cài trong container.
