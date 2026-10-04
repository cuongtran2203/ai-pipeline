---
name: ai-pipeline-report
description: Report contract for the AI pipeline workflow - after EVERY experiment round write a Vietnamese report.md in the fixed 3-part template (Tổng quan / Nội dung chi tiết / Kết luận) plus a report.html that visualizes error clusters; both generated from eval.json.
---

# Reports

**After EVERY experiment round, without exception** (train/eval/probe/ablation, each optimize round, phase-end evaluation) write both files into `runs/<id>/reports/round-NN-<slug>/` (and the module dir when the round belongs to a module). A round without both files is unfinished: the next round must not start. Produce them from one `eval.json` (schema: `schemas/eval.schema.json`):
`python scripts/render_report.py <eval.json> --out-dir <dir>` (template: `templates/report.template.md`). Ngôn ngữ: `eval.json` `lang`, fallback `--lang`, fallback vi.

## report.md — Markdown by default (docx only if the user asks), short and clear, exactly 3 parts
1. **Tổng quan** (tiếng Anh khi `lang: en`: Overview/Approach/Results) — *Hiện trạng bài toán* (what the previous version achieved, on which eval set; the issues being solved) · *Phương pháp giải quyết* (concrete change per issue: data, training, schema, postprocess or evaluation) · *Kết quả đạt được* (before/after with measured numbers; what improved, what remains; not measured ⇒ "chưa đánh giá" / "N/A"). Khi có `eval_contract`: thêm mục *Hợp đồng đánh giá* (đơn vị, split, metric + hướng tốt, slice/horizon, scorer, độ bất định, nguồn gốc).
2. **Nội dung chi tiết**
   - *Bảng metric chi tiết*: one row = one evaluation item (metric/slice; ví dụ OCR/KIE: one field of one document type); columns scope, item, metric (+hướng tốt ↑/↓), sample count, previous version, current version, change. Same metric and same eval set when comparing; if they differ say so and draw no up/down conclusion. No evidence ⇒ `N/A`, never invent numbers.
   - *Phân tích lỗi theo nhóm*: table of error group · count/rate (state the denominator; thiếu thì N/A) · affected scope · evidence-backed cause · one typical example. Only groups that really occur.
3. **Kết luận** — fixes in priority order, each line: **priority → error group → what to do → how to verify**. Blockers of evaluation and the largest groups first; no vague "improve data / optimize model".

Do not narrate the execution diary and do not paste long logs: keep only numbers, evidence and the artifact links needed to check the conclusions.

## report.html
Focused on **visualizing the error clusters** (distribution bars, one card per cluster with cause and concrete examples; optional `image` per example). Not a copy of the md.

## eval.json notes
`overview.status|method|result` ⇒ the three Tổng quan bullets. `eval_contract` (optional) `{unit, split{strategy random|group|temporal|rolling-origin, details}, metrics[{name, direction higher|lower}], slices[], horizon, scorer{kind human|model|rule, details}, uncertainty{method, level}, provenance{}}`. `tables[].name` = scope (slice/horizon; ví dụ OCR/KIE: document type), `tables[].metric`/`n`/`eval_set`, rows `{item, metric, direction, n | correct,total | value+unit, ci, baseline_correct | prev_value, prev_metric/prev_set}`; a row with no numbers renders `N/A`. `errors[]` `{cluster,count,denominator,fields,cause,example|examples}`. `conclusion.fixes[]` `{priority,error,fix,measure}`; optional `artifacts[]` `{label,path}`.
