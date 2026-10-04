---
name: ai-pipeline-report
description: Report contract for the AI pipeline workflow - Vietnamese report.md with 3 fixed sections plus report.html visualizing clustered errors; generated from eval.json.
---

# Reports

**After EVERY experiment round, without exception** (train/eval/probe/ablation, each optimize round, phase-end evaluation), write both files into `runs/<id>/reports/round-NN-<slug>/` (and the module dir when the round belongs to a module). A round without both files is unfinished: the next round must not start. Produce them from one `eval.json`:
`python scripts/render_report.py <eval.json> --out-dir <dir>`

- `report.md` (Vietnamese, **concise**; it must make three things explicit: **how the experiment was run** → `overview.method`, **which problem it solved / did not solve** → `overview.solved`, **detailed results** → tables + errors. Keep ≤ ~1–2 screens of prose; numbers live in tables): **1. Tổng quan** (vòng thí nghiệm, hiện trạng, thí nghiệm thế nào, giải quyết được vấn đề gì, kết quả) · **2. Nội dung chi tiết** (bảng độ chính xác từng thành phần/field: đúng/tổng, %, baseline, Δ; lỗi tồn đọng) · **3. Kết luận** (lỗi còn lại, giải pháp + ưu tiên + cách đo, có nên dùng checkpoint mới không). Template: `templates/report.template.md`.
- `report.html`: focused on **visualizing the error clusters** (distribution bars, one card per cluster with cause and concrete examples; not a copy of the md): error clusters, distribution bars, concrete examples (optional `image` per example).

Rules: same evaluation set for baseline and new; state sample counts; separate confirmed causes from hypotheses; version model and dataset in the header.
