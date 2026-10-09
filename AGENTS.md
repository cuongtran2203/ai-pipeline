# AI Pipeline Workflow — agent instructions (Claude Code + Codex)

This repo is a multi-agent workflow: **spec file in → plan + parallel execution (Orca) out**, for projects that build AI pipelines (CV/NLP). Source: `AI_workflow_proposal.md`.

## Start
User says "run ai-pipeline on <spec>" → use skill `ai-pipeline` (`skills/ai-pipeline/SKILL.md`; mirrored in `.claude/skills` and `.agents/skills`). Edit only `skills/`, then `python scripts/sync_skills.py`.

## Chuẩn v2: tổng quát, graph engineering, human-on-the-loop
- **Tổng quát (mọi loại dự án AI):** đánh giá theo `schemas/eval.schema.json` (đơn vị, split `random|group|temporal|rolling-origin`, metric + hướng tốt, slice/horizon, scorer human|model|rule, độ bất định, provenance); task có execution mode `train|evaluate-only|retrieve-only|inference-service|monitor`; ngôn ngữ báo cáo cấu hình theo run; registry role `roles/registry.json`; ví dụ phi OCR trong `examples/`.
- **Graph engineering:** tri thức dự án là đồ thị có kiểu append-only `runs/<id>/knowledge/` (`scripts/kg.py`, skill `ai-pipeline-knowledge`): 8 loại node, 8 loại cạnh (depends_on, uses, evaluated_on, decided_by, approved_by, supersedes, caused, evidenced_by), hiệu lực thời gian `valid_from/valid_to/recorded_at`, truy vấn `neighbors|path|explain|timeline --as-of`. Truy nguyên quyết định/evidence bằng truy vấn, không đoán; cạnh sai bị từ chối. `plan.json deps` chỉ là DAG điều phối, không phải cạnh tri thức.
- **Human-on-the-loop:** `autonomy_policy.json` theo run (mode theo phase, cap, ngưỡng cảnh báo, version) + `audit.jsonl` append-only + `scripts/supervisor.py` (chỉ đọc) với `scripts/orca_snapshot.py`; skill `ai-pipeline-autonomy`. G1/G2/G3 vẫn bắt buộc; ngoài gate pipeline tự chạy trong policy, người giám sát qua cảnh báo.

## Cưỡng chế, eval cấu hình, giám sát sau release (chuẩn v3)
- **Hook cưỡng chế** (`scripts/pipeline_guard.py`, skill `ai-pipeline-hooks`, cài bằng `ai-pipeline hooks install`): PreToolUse chặn bằng máy việc đọc nhãn test, cài gói trên host, docker nguy hiểm, ghi ngoài ownership, sửa test để làm xanh, lệnh phá hoại; role lấy từ `task_context.json` (`pipeline_guard.py context write <run_dir>`), không từ tham số tự khai; mọi quyết định ghi `guard_audit.jsonl`. Hook là bộ lọc lệnh, không phải sandbox thật (xem giới hạn trong skill); Codex không có hook tương đương: chạy `pipeline_guard.py --check` thủ công/CI.
- **Eval cho cấu hình agent** (`scripts/evals.py`, skill `ai-pipeline-evals`): `python scripts/evals.py run --static` phải xanh trước khi commit đổi `skills/`, `roles/`, `AGENTS.md`, `templates/`, hook; case hành vi trong `evals/cases/` chấm theo event tool có cấu trúc (transcript văn xuôi = inconclusive); mỗi sự cố thật thành một case (`evals.py add-incident`).
- **Giám sát sau release** (`scripts/monitor.py`, skill `ai-pipeline-monitor`): control band 1σ/2σ/3σ + drift PSI/KS theo `monitor_policy.json` do người duyệt ở G2; vượt band mở incident (intent) tái nhập pipeline qua G1/G2, không tự rollback hay tự sửa.

## Vòng tối ưu bắt buộc sau baseline (chuẩn v4)
Sau baseline trên **val/OOF** (không bao giờ trên test khóa), pipeline TỰ lặp: phân tích lỗi → chẩn đoán → cải tiến (retrain, hậu xử lý, hoặc thêm module — vd. OCR kém chữ số viết tay → research dataset → train bản phân loại → định tuyến theo độ tin cậy) → đánh giá lại, bằng `python scripts/optimize.py init|status|next --apply|record|reconcile` (skill `ai-pipeline-optimize`). Dừng khi đạt target, chạm ceiling (hỏi người), hết hiệu quả cận biên, hết vòng/ngân sách. Gain tính có dấu, vòng làm tụt field khác bị bác bỏ; dữ liệu ngoài đi qua `research → gate người duyệt nguồn → use-check provenance (scripts/data_provenance.py)` rồi mới train; tập test khóa chỉ chạm một lần ở bước cuối.

## Sơ đồ cho report pipeline/kiến trúc mô hình (chuẩn v5)
Khi làm report/tài liệu về pipeline hoặc kiến trúc mô hình, dùng skill `ai-pipeline-diagram` (`scripts/diagram.py render|validate|from-plan|from-model`): sinh `.excalidraw` + `.svg` (nhúng vào `report.html`) + `.excalidraw.md` (Obsidian). Quy tắc cứng: không dùng hình thoi (cổng/quyết định = chữ nhật bo góc nét đứt), nhãn gắn hai chiều với khối, mũi tên vuông góc nối đúng mép và không xuyên hộp, tối đa ~25 node mỗi sơ đồ (lớn hơn tự tách theo phase). Luôn `validate` trước khi nhúng; render PNG hay công thức LaTeX cần cài thêm nên phải hỏi người trước.

## Thành phần yếu: chẩn đoán trước khi sửa
Khi lên plan, architect lên sẵn playbook xử lý cho cả pipeline (`playbook.json` + sơ đồ `scripts/playbook_diagram.py`, người dùng duyệt ở G2). Thành phần dưới mục tiêu → cử agent `weakness-diagnostician` (skill `ai-pipeline-diagnose`) xác minh bằng thí nghiệm phân biệt (áp dụng cho mọi loại dự án AI): DATA (ít dữ liệu thật / thiếu phủ lát cắt / data sinh lệch phân bố), MODEL (thiếu năng lực, quy mô, độ phân giải, ngữ cảnh), STRUCTURE (cần tách cấu trúc hoặc model/module phụ trợ), OBJECTIVE (metric/ngưỡng/spec lệch), hay NOISE (nhãn mơ hồ); rồi chọn nhánh hành động đã duyệt. Không sửa theo cảm tính.

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
