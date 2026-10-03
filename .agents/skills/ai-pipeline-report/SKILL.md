---
name: ai-pipeline-report
description: Report contract for the AI pipeline workflow - Vietnamese report.md with 3 fixed sections plus report.html visualizing clustered errors; generated from eval.json.
---

# Reports

After every training/evaluation/summary produce two files from one `eval.json`:
`python scripts/render_report.py <eval.json> --out-dir <dir>`

- `report.md` (Vietnamese): **1. Tổng quan** (hiện trạng, phương pháp, kết quả) · **2. Nội dung chi tiết** (bảng độ chính xác từng thành phần/field: đúng/tổng, %, baseline, Δ; lỗi tồn đọng) · **3. Kết luận** (lỗi còn lại, giải pháp + ưu tiên + cách đo, có nên dùng checkpoint mới không). Template: `templates/report.template.md`.
- `report.html`: error clusters, distribution bars, concrete examples (optional `image` per example).

Rules: same evaluation set for baseline and new; state sample counts; separate confirmed causes from hypotheses; version model and dataset in the header.
