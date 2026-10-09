---
name: ai-pipeline-report
description: Report contract for the AI pipeline workflow - after EVERY experiment round write a Vietnamese report.md in the fixed 3-part template (Tổng quan / Nội dung chi tiết / Kết luận) plus a report.html that visualizes error clusters (both from eval.json), and after every training round archive three HTML docs - data_report.html (thống kê dữ liệu), method_report.html (phương pháp nghiên cứu), results_report.html (kết quả nghiên cứu) - from templates/round_*_report.template.html.
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

## Lưu trữ sau mỗi round huấn luyện thử nghiệm: thống kê dữ liệu · phương pháp · kết quả

Sau **mỗi round train thử nghiệm** (kể cả round thất bại hoặc bị revert), ngoài `report.md` + `report.html`, phải lưu thêm 3 tài liệu HTML độc lập vào cùng `runs/<id>/reports/round-NN-<slug>/` theo mẫu trong `templates/`:

| File | Mẫu | Nội dung |
|---|---|---|
| `data_report.html` | `round_data_report.template.html` | Thông tin bộ dữ liệu + cách gán nhãn · cấu trúc thư mục · số lượng train/val/test · phân phối (train vs test, label) · ảnh/mẫu minh họa. Dữ liệu kế thừa version trước thì ghi rõ phần thay đổi (khối `.note`) |
| `method_report.html` | `round_method_report.template.html` | Tên phương pháp · bài toán + khó khăn có chẩn đoán · cải tiến so với cách cũ · sơ đồ train/inference · loss + metric · bảng so sánh với phương pháp khác (cùng tập, cùng script chấm) · references |
| `results_report.html` | `round_results_report.template.html` | Bảng đầu trang (project, version, ngày, tác giả) · Conclusions (Achievements / Methods / Difficulties & next plans) · bảng Experiments so các version (cột metric, strategy, config, input, augmentation; ô tốt nhất/kém nhất tô màu) · Bad cases · Good cases · Attached files · Checklist release |

Quy trình:
1. `python scripts/round_docs.py init <round_dir> --set ROUND_TITLE="..." --set PROJECT="..." --set VERSION="..."` (sinh 3 file, không ghi đè file đã có; `--force` để ghi đè).
2. Điền từng placeholder `{{KEY}}` và xoá các khối `.hint` bằng **số liệu đã đo** từ `eval.json`, `notebook/`, log train; chưa đo thì `N/A`, không suy diễn. Thêm/bớt hàng, cột theo thực tế round.
3. `python scripts/round_docs.py check <round_dir>` phải `OK` (đủ 3 file, không còn placeholder) rồi mới coi round là xong và được sang round sau.

Quy tắc nội dung: số liệu so sánh phải cùng tập đánh giá + cùng metric + cùng script chấm; test khoá chỉ báo ở bước cuối; version đã revert vẫn ghi lý do bỏ ở *Difficulties*; không đưa mật khẩu/secret hay dữ liệu nhạy cảm vào HTML (ảnh minh họa phải đã được phép dùng); sơ đồ dùng skill `ai-pipeline-diagram`. Ba file tự đủ để người khác hiểu round mà không cần đọc nhật ký: ghi vào sổ thí nghiệm (`ai-pipeline-notebook`) đường dẫn tới chúng.

## Sơ đồ pipeline / kiến trúc mô hình (tùy chọn, khuyến khích)

Khi report nói về **pipeline** (luồng task/dữ liệu) hoặc **kiến trúc mô hình**, tạo sơ đồ bằng
skill `ai-pipeline-diagram` (`python scripts/diagram.py render|from-plan|from-model ...`,
`python scripts/diagram.py validate ...`, chỉ nhúng file đã VALID, không diamond) và khai
`diagrams: [{title, svg, excalidraw?}]` trong `eval.json` (schema: `schemas/eval.schema.json`).
`report.html` nhúng SVG nội tuyến (không tải ngoài), `report.md` chèn liên kết tới file;
file sơ đồ đặt trong `runs/<id>/reports/round-NN-<slug>/diagrams/`. Thiếu trường `diagrams`
thì hành vi cũ không đổi.

## eval.json notes
`overview.status|method|result` ⇒ the three Tổng quan bullets. `eval_contract` (optional) `{unit, split{strategy random|group|temporal|rolling-origin, details}, metrics[{name, direction higher|lower}], slices[], horizon, scorer{kind human|model|rule, details}, uncertainty{method, level}, provenance{}}`. `tables[].name` = scope (slice/horizon; ví dụ OCR/KIE: document type), `tables[].metric`/`n`/`eval_set`, rows `{item, metric, direction, n | correct,total | value+unit, ci, baseline_correct | prev_value, prev_metric/prev_set}`; a row with no numbers renders `N/A`. `errors[]` `{cluster,count,denominator,fields,cause,example|examples}`. `conclusion.fixes[]` `{priority,error,fix,measure}`; optional `artifacts[]` `{label,path}`.
