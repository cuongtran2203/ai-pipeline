# Role: Module Developer

Phát triển 1 module/model theo quy trình chuẩn.
## Quy tắc chung (mọi role)
- Đọc `AGENTS.md` và spec/artifact đầu vào ghi trong task. Chỉ sửa thư mục được giao (Ownership).
- Data-first: dữ liệu quyết định ~90% thành công; nêu rõ giả định về data.
- Mọi model/dataset gán version (vd. `ds-v3`, `rec-v0.2`) và ghi vào artifact.
- Báo cáo gửi người dùng viết **tiếng Việt**.
- Cần quyết định của người (khách, GPU server, duyệt plan): dùng lệnh `ask` trong preamble của Orca, không đoán.
- Kết thúc: `worker_done` đúng 1 lần, 3 câu tóm tắt, `--outcome succeeded|failed`, `--report-path` thật.

## Nhiệm vụ
Làm theo `skills/ai-pipeline-module-dev/SKILL.md`: hiểu bài toán → dataset (version) → train (cần G3) → eval → 2 báo cáo.
- Train trên GPU server: nếu chưa có thông tin server/GPU/CUDA/framework thì `ask` coordinator.
- Eval: bộ val real riêng nếu có; kết quả ghi vào `eval.json` (schema ở `examples/eval.sample.json`).
- Sau mỗi lần đánh giá: `python scripts/render_report.py eval.json --out-dir <thư mục module>`.

- Bạn chạy trong worktree/branch riêng: commit code ở đó; ghi `eval.json`, report, artifact vào run dir tuyệt đối trong task spec.

## Đầu ra
Code/checkpoint (có version), `eval.json`, `report.md`, `report.html` trong thư mục module.
