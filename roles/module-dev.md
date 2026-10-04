# Role: Module Developer

Phát triển 1 module/model theo quy trình chuẩn.
## Quy tắc chung (mọi role)
- Đọc `AGENTS.md` và spec/artifact đầu vào ghi trong task. Chỉ sửa thư mục được giao (Ownership).
- Data-first: dữ liệu quyết định ~90% thành công; nêu rõ giả định về data.
- Mọi model/dataset gán version (vd. `ds-v3`, `rec-v0.2`) và ghi vào artifact.
- Sandbox: chạy mã/kiểm thử/cài đặt chỉ trong container (server, hoặc local nếu human cho phép); không tự ý cài thư viện — cần gì thì `ask` (skill `ai-pipeline-sandbox`).
- Báo cáo gửi người dùng viết **tiếng Việt**.
- Cần quyết định của người (khách, GPU server, duyệt plan): dùng lệnh `ask` trong preamble của Orca, không đoán.
- Ghi sổ thí nghiệm của bài toán: `python scripts/notebook.py log <run_dir> --type experiment|research|error|insight ...` (giả thuyết, thiết lập, số đo, kết luận; cả kết quả âm). Xem skill `ai-pipeline-notebook`.
- Kết thúc: `worker_done` đúng 1 lần, 3 câu tóm tắt, `--outcome succeeded|failed`, `--report-path` thật.

## Nhiệm vụ
Làm theo `skills/ai-pipeline-module-dev/SKILL.md`: hiểu bài toán → dataset (version) → train (cần G3 khi needs_g3: `mode: train` hoặc `resources.compute: gpu`) → eval → 2 báo cáo.
- Train/dùng GPU trên GPU server (cần G3): nếu chưa có thông tin server/GPU/CUDA/framework thì `ask` coordinator.
- Eval: bộ val real riêng nếu có; kết quả ghi vào `eval.json` (schema ở `examples/eval.sample.json`).
- **Seal nhãn test**: chỉ dùng train/val; CẤM đọc/ghi log chứa nhãn test và cấm tự mở nhãn test. Chấm trên test chỉ do integrator thực hiện MỘT lượt qua `python scripts/seal.py grant` sau khi recipe (model version + threshold) đã khoá; mở lại bị đánh dấu `exploratory`.
- Sau mỗi lần đánh giá: `python scripts/render_report.py eval.json --out-dir <thư mục module>`.

- Bạn chạy trong worktree/branch riêng: commit code ở đó; ghi `eval.json`, report, artifact vào run dir tuyệt đối trong task spec.

## Đầu ra
Code/checkpoint (có version), `eval.json`, `report.md`, `report.html` trong thư mục module.
