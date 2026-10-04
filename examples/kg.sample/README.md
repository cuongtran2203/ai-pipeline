# Ví dụ Đồ thị Tri thức Mẫu (Typed Knowledge Graph Sample)

Thư mục này chứa đồ thị tri thức mẫu được trích xuất (backfilled) từ `runs/timesheet-ocr`, chứng minh khả năng quản lý thực thể có kiểu, cạnh quan hệ có kiểm soát và truy vết lịch sử tiến hóa.

## Cấu trúc dữ liệu
- `knowledge/entities.jsonl`: 47 thực thể (Run, Task, Decision, Experiment, Artifact, Incident, Person, Policy).
- `knowledge/edges.jsonl`: 47 cạnh có kiểu (`depends_on`, `uses`, `evaluated_on`, `decided_by`, `approved_by`, `supersedes`, `caused`, `evidenced_by`).

## Các câu hỏi mẫu kiểm chứng (Traceability Questions)

### 1. Quyết định nào thay thế mục tiêu ban đầu của break_time và dựa trên bằng chứng nào?
```sh
python scripts/kg.py explain examples/kg.sample decision:break-time-norm-min
```
**Kết quả**:
- Thay thế (`supersedes`): `decision:G1-target-raw`
- Bằng chứng (`evidenced_by`): `exp:A1-raw-break-analysis` (Khảo sát 504 dòng thấy nhãn KIE ngẫu nhiên 1h/60/1:00, trần thô chỉ 78.97%)
- Người quyết định (`decided_by`): `person:data-analyst`, người duyệt (`approved_by`): `person:user`

### 2. Sự cố cài gói ngoài host phát sinh từ task nào và dẫn đến quyết định gì?
```sh
python scripts/kg.py path examples/kg.sample task:RT decision:remove-host-packages
```
**Kết quả**:
- Đường đi 2 chặng: `task:RT` --(caused)--> `incident:host-package-install` --(caused)--> `decision:remove-host-packages`

### 3. Vì sao kế hoạch sinh dữ liệu D2 bị dừng và quyết định nào thay thế?
```sh
python scripts/kg.py explain examples/kg.sample decision:cancel-mh2-keep-rec-v0.1
```
**Kết quả**:
- Thay thế (`supersedes`): `decision:add-d2-synthetic-gen`
- Bằng chứng (`evidenced_by`): `exp:D2-ablation-synth` (Thực nghiệm ablation chứng minh dữ liệu sinh không cải thiện rec-hw)
- Quyết định bởi: `person:coordinator`, duyệt bởi: `person:user`

## Kiểm tra tính toàn vẹn
```sh
python scripts/kg.py validate examples/kg.sample
```
