---
name: ai-pipeline-research
description: Nghiên cứu dataset NGOÀI có kiểm soát cho ai-pipeline. Dùng khi chẩn đoán ra DATA/STRUCTURE cần dataset công khai (vd chữ số viết tay để train bản phân loại cho field số của OCR): researcher tìm + xếp hạng ứng viên, người duyệt nguồn/giấy phép, worker tải trong container, đăng ký provenance (data_provenance.py), rồi thí nghiệm nhỏ kiểm lệch phân bố trên val thật trước khi tin. Không "tải bừa".
---

# Dataset ngoài có kiểm soát (provenance + con người duyệt)

Mục tiêu: khi `ai-pipeline-diagnose` kết luận **DATA** hoặc **STRUCTURE** cần dữ liệu ngoài, ta thêm dữ liệu đó một cách **truy vết được**, **có người duyệt**, và **kiểm chứng bằng thực nghiệm** thay vì tải bừa rồi hy vọng.

Nguyên tắc bất di bất dịch:
- **Dữ liệu ngoài chỉ để train/pretrain.** KHÔNG bao giờ đưa vào val/test của dự án. Mọi số đo vẫn trên **val thật** (hoặc OOF) của dự án; test khóa chỉ chạm 1 lần ở tích hợp cuối.
- **`data_provenance.py` không tải và không dùng mạng.** Việc tải do worker trong container (luật `ai-pipeline-sandbox`) sau khi người duyệt; script chỉ ghi nhận + kiểm.
- **Người duyệt nguồn + giấy phép** là cổng bắt buộc (dùng `ask` (`scripts/worker_done.py`), ghi `decisions.md`). Approver phải là người: `approved_by = person:<tên>`.
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

## Quy trình tìm tài liệu có kỷ luật

Dùng khi cần tìm **bài báo/tiền lệ (prior art)** cho một câu hỏi kỹ thuật (kiến trúc, loss, augmentation, mốc ceiling công khai, dataset) rồi mới kết luận — không quét vô hạn, không bịa nguồn.

**Ngân sách vòng truy vấn** (theo độ khó câu hỏi; 1 vòng = một nhóm truy vấn tinh chỉnh dựa trên kết quả vòng trước):
- Dễ (định nghĩa, 1 khái niệm): **0 vòng** bổ sung.
- Trung bình (so sánh 2 phương án, 1 lĩnh vực): **1 vòng**.
- Khó (nhiều lĩnh vực, chủ đề mới, nhiều nhánh): **2 vòng**.
- **Trần 4 vòng mỗi lượt.** Hết ngân sách mà chưa đủ phủ → kết luận tạm kèm `chưa xác minh`, không quay vô hạn.

**Chiến lược nhiều nguồn công khai** (chọn theo lĩnh vực; web search chung **chỉ là dự phòng** khi các nguồn dưới không trả gì):
- arXiv/alphaXiv: CS/AI/ML, có toàn văn.
- OpenAlex: liên ngành, đồ thị trích dẫn, metadata phong phú.
- PubMed (NCBI E-utilities): y sinh.
- bioRxiv: preprint sinh học (thường lần qua chỉ mục OpenAlex).
Một vòng khó nên dùng ≥2 nguồn khác loại. Mỗi nguồn ghi rõ đã truy cập lúc nào và URL/DOI gốc.

**Khử trùng lặp** đúng thứ tự: `id` (arXiv id/PMID/OpenAlex `W…`/DOI) → `DOI` chuẩn hoá (bỏ tiền tố `https://doi.org/`, lowercase) → **tiêu đề chuẩn hoá** (lowercase, bỏ dấu câu/khoảng trắng thừa). Bản trùng giữ bản đầy đủ metadata nhất và nguồn phát hiện đầu tiên.

**Luật dừng sớm:** dừng thêm vòng khi ~3 kết quả mới liên tiếp không đổi kết luận, hoặc đã đủ phủ các nhánh (kiến trúc/dữ liệu/metric) của câu hỏi. Không kéo dài cho "đủ số".

**Đọc sâu trước khi kết luận:** đọc kỹ **3–5 bài then chốt (load-bearing)** — bài trực tiếp đề xuất phương pháp, bài mới/dẫn nhiều — trước khi tổng hợp claim; xem figure/table của bài, không kết luận chỉ từ abstract.

**Luật trích dẫn (bắt buộc):**
- Mỗi claim có nguồn kèm **trích nguyên văn ngắn** (một câu/đoạn ngắn trong ngoặc kép) + **số trang hoặc số mục/bảng** + **URL/DOI** trỏ tới nguồn gốc (arXiv/doi.org/openalex.org/pubmed). Không có số trang (HTML/preprint) thì ghi rõ `không có số trang (HTML)`.
- **Cấm bịa trích dẫn hoặc số liệu.** Không chắc → `chưa xác minh`. Chỉ dùng số đọc được từ nguồn, không suy từ trí nhớ.
- **Không so sánh số vote của alphaXiv với số citation của OpenAlex** — hai hệ đo khác nhau; nếu cần độ phổ biến thì so *trong cùng một nguồn* và nói rõ.
- Link trích dẫn trỏ tới nguồn gốc, không trỏ trang trung gian chưa kiểm chứng.

**Từ bài báo lần ra dataset:**
- Paper thường dẫn dataset qua: (a) repo/GitHub liên kết (mục "Code/Data availability"), (b) bảng dataset trong paper (tên, size, split, license nếu có), (c) URL trong tài liệu tham khảo.
- Ứng viên dataset (tên, nguồn, license, size, domain) ghi vào `research/datasets.md`, **rồi** đăng ký provenance:
  `python scripts/data_provenance.py register <run_dir> --card <card.json> --requested-by researcher`
- `source_url` lấy từ bằng chứng paper/repo; đăng ký **không** thay cổng người duyệt (`approve`) hay `use-check` trước khi train.

## Quyền riêng tư truy vấn

- Truy vấn tìm tài liệu (kể cả web search) **rời máy tới bên thứ ba**. Truy vấn **CHỈ** được chứa từ khoá chủ đề chung (vd "handwritten digit recognition benchmark").
- **KHÔNG** đưa vào truy vấn/URL: tên khách hàng hay dự án, nội dung dữ liệu có thể nhận dạng, tên file/thư mục nội bộ, mẫu dữ liệu, định danh (id) nhạy cảm, hay chi tiết chỉ có trong run. Không dán nguyên văn dữ liệu khách hàng vào truy vấn.
- **Liệt kê mọi truy vấn đã dùng vào sổ** (kể cả truy vấn không ra kết quả):
  `python scripts/notebook.py log <run_dir> --type research --title "Truy vấn tài liệu" --body "<nguồn + truy vấn + ngân sách vòng>" --author researcher`
- Buộc phải tìm theo ngữ cảnh nhạy cảm → hỏi người (`ask`) trước, không tự gửi.

## Nguồn ý tưởng

- Quy trình trên (ngân sách vòng truy vấn, khử trùng `id → DOI → tiêu đề`, luật dừng sớm, đọc sâu 3–5 bài, luật trích dẫn, quyền riêng tư truy vấn) **tham chiếu ý tưởng** từ dự án mã nguồn mở `alphaXiv/OpenResearch`, commit `951700e`, skill `orx-lit-review`; giấy phép **MIT** (`LICENSE`).
- Hợp đồng launch (xem `skills/ai-pipeline-sandbox/SKILL.md` và `skills/ai-pipeline-module-dev/SKILL.md`) tham chiếu thêm `orx-evidence` và `orx-experiment-tree`.
- Đây là **bản viết lại bằng lời của ta**, không sao chép nguyên văn; ta dùng công cụ của mình (web/HTTP + `notebook.py` + `data_provenance.py`), **không cài và không gọi binary `orx`**.

## Khi nào DỪNG / KHÔNG làm
- Không tải dữ liệu trước khi người duyệt.
- Không đưa dữ liệu ngoài vào val/test.
- Thí nghiệm nhỏ không cải thiện trên val thật → bỏ dataset, đừng cố ép.
- Vượt ngân sách/`cap` của playbook hay dự đoán lợi ích < sai số chuẩn của bộ đánh giá → dừng, báo người.
- Không tự rollback/sửa trong production vì lệch phân bố; mở incident → intent tái nhập pipeline.
