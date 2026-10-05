---
name: ai-pipeline-research
description: Nghiên cứu dataset NGOÀI có kiểm soát cho ai-pipeline. Dùng khi chẩn đoán ra DATA/STRUCTURE cần dataset công khai (vd chữ số viết tay để train bản phân loại cho field số của OCR): researcher tìm + xếp hạng ứng viên, người duyệt nguồn/giấy phép, worker tải trong container, đăng ký provenance (data_provenance.py), rồi thí nghiệm nhỏ kiểm lệch phân bố trên val thật trước khi tin. Không "tải bừa".
---

# Dataset ngoài có kiểm soát (provenance + con người duyệt)

Mục tiêu: khi `ai-pipeline-diagnose` kết luận **DATA** hoặc **STRUCTURE** cần dữ liệu ngoài, ta thêm dữ liệu đó một cách **truy vết được**, **có người duyệt**, và **kiểm chứng bằng thực nghiệm** thay vì tải bừa rồi hy vọng.

Nguyên tắc bất di bất dịch:
- **Dữ liệu ngoài chỉ để train/pretrain.** KHÔNG bao giờ đưa vào val/test của dự án. Mọi số đo vẫn trên **val thật** (hoặc OOF) của dự án; test khóa chỉ chạm 1 lần ở tích hợp cuối.
- **`data_provenance.py` không tải và không dùng mạng.** Việc tải do worker trong container (luật `ai-pipeline-sandbox`) sau khi người duyệt; script chỉ ghi nhận + kiểm.
- **Người duyệt nguồn + giấy phép** là cổng bắt buộc (dùng `ask` của Orca, ghi `decisions.md`). Approver phải là người: `approved_by = person:<tên>`.
- **Không bịa nguồn.** Không chắc URL → ghi `chưa xác minh`.

## Quy trình (7 bước)

1. **Kích hoạt.** Từ `diagnosis.json` verdict DATA/STRUCTURE (hoặc `ceiling.json`), xác định *dữ liệu thiếu gì* (loại mẫu, lát cắt, ngôn ngữ/bộ ký tự, độ phân giải…) và module đích (vd bộ phân loại chữ số định tuyến theo ngưỡng tin cậy). Ghi giả thuyết vào sổ: `python scripts/notebook.py log <run_dir> --type research --title "..." --body "..." --author researcher`.

2. **Researcher tìm + xếp hạng.** Chạy chế độ `dataset-research` (role `roles/researcher.md`). Đầu ra `runs/<id>/research/datasets.md`, xếp hạng theo: độ giống miền đích (nguồn/người viết, ngôn ngữ/bộ ký tự, định dạng crop, độ phân giải), giấy phép & hạn chế, kích thước, **nguy cơ lệch phân bố**, cách kiểm bằng thí nghiệm nhỏ, lợi ích dự đoán. Nguồn tham chiếu thật; chưa kiểm thì ghi `chưa xác minh`.

3. **Người duyệt nguồn + giấy phép (cổng).** Trình `datasets.md` + 3–5 dòng/ứng viên. Hỏi qua `ask` (vd "*Duyệt dataset X (license ..., dùng để train) và phương án tải trong container?*"), ghi kết quả vào `decisions.md`. Không tự tải trước khi được duyệt.

4. **Tải trong container + thí nghiệm nhỏ.** Worker tải dataset trong container (không cài gói khi chưa hỏi), rồi chạy **thí nghiệm lệch phân bố NHỎ** trên **val thật của dự án**: so baseline vs (a) train/pretrain thêm dữ liệu ngoài, (b) chỉ dùng dữ liệu gốc. Đo trên val thật, có ≥2 seed nếu được; nếu thêm dữ liệu ngoài không cải thiện hoặc làm tệ hơn → **bỏ**, ghi kết quả âm vào sổ.

5. **Đăng ký provenance.** Điền card theo `templates/dataset_card.template.json` (bắt buộc `domain_shift_assessment`, `version`, `license`, hash file/thư mục), rồi:
   `python scripts/data_provenance.py register <run_dir> --card <card.json> --requested-by researcher`
   Người duyệt: `python scripts/data_provenance.py approve <run_dir> <id> --approver "person:<tên người>"`
   Kiểm hash (không mạng): `python scripts/data_provenance.py verify <run_dir> <id>`
   Xem registry: `python scripts/data_provenance.py list <run_dir>`

6. **Cổng trước khi train.** `module-dev`/`optimize` PHẢI gọi `python scripts/data_provenance.py use-check <run_dir> <id>` trước khi train. `use-check` chặn nếu: chưa duyệt, `license_ok=false`, thiếu `domain_shift_assessment`, hoặc hash không khớp. Chỉ tiếp tục khi OK.

7. **Tích hợp + báo cáo.** Nếu thí nghiệm nhỏ cho thấy lợi ích, thêm module/định tuyến đã duyệt vào pipeline, đo lại trên val thật, và ghi provenance (id dataset ngoài + version + license + kết quả pilot) vào `report.md`/`report.html` của vòng. Cập nhật đồ thị tri thức đã tự động qua `register`/`approve`.

## Registry & trường bắt buộc
State: `runs/<id>/external_data/registry.json`. Mỗi entry:
`id, name, version, source_url, license, license_ok, retrieved_at, archive_sha256` (hoặc `manifest` hash từng file), `size_bytes, domain, intended_use, domain_shift_assessment, approved_by, location` (đường dẫn trong container), `pilot_result`.

- `register` ghi **Artifact + Decision** (decided_by Person) vào KG qua API `scripts/kg.py` (`add_edge_checked`/`upsert_entity`) và 1 dòng sổ.
- `approve` chỉ do người; ghi **Decision** (approved_by/decided_by Person).
- Ghi registry qua `statefile.update_json` (atomic, khoá) → hai register đồng thời không mất entry.

## Khi nào DỪNG / KHÔNG làm
- Không tải dữ liệu trước khi người duyệt.
- Không đưa dữ liệu ngoài vào val/test.
- Thí nghiệm nhỏ không cải thiện trên val thật → bỏ dataset, đừng cố ép.
- Vượt ngân sách/`cap` của playbook hay dự đoán lợi ích < sai số chuẩn của bộ đánh giá → dừng, báo người.
- Không tự rollback/sửa trong production vì lệch phân bố; mở incident → intent tái nhập pipeline.
