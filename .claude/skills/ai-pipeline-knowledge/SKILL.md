---
name: ai-pipeline-knowledge
description: Lớp tri thức đồ thị có kiểu (Typed Knowledge Graph) cho AI Pipeline - quản lý thực thể, 8 loại cạnh kiểm soát, tính hợp lệ thời gian (valid_from/valid_to/as-of) và truy nguyên quyết định/bằng chứng (decided_by, supersedes, caused, evidenced_by). Bắt buộc dùng trong status, planning và diagnose.
---

# Typed Knowledge Graph (Graph Engineering Layer)

Lớp tri thức đồ thị có kiểu giúp toàn bộ hệ thống multi-agent vượt qua giới hạn của tìm kiếm tương đồng phẳng (vector search) hay grep từ khóa. Bằng cách lưu trữ thực thể và các cạnh quan hệ có kiểu (typed edges), hệ thống có khả năng suy luận đa chặng (multi-hop traversal) và truy vết lịch sử: **ai đã quyết định điều này, quyết định nào thay thế nó, sự cố nào gây ra, và bằng chứng thực nghiệm nào chứng minh**.

---

## 1. Phân biệt 3 lớp đồ thị trong AI Pipeline

Tuyệt đối không nhầm lẫn 3 lớp đồ thị sau:

| Lớp đồ thị | Lưu trữ & Công cụ | Bản chất & Mục đích | Loại quan hệ tiêu biểu |
|---|---|---|---|
| **1. Knowledge Graph (Tri thức)** | `runs/<id>/knowledge/` (`kg.py`) | Bộ nhớ ngữ nghĩa và nhân quả của dự án; ghi nhận quyết định, bằng chứng, sự cố và tính hợp lệ bitemporal. | `decided_by`, `supersedes`, `caused`, `evidenced_by`, `evaluated_on`, `uses`, `approved_by` |
| **2. Workflow DAG (Điều phối)** | `plan.json`, Orca tasks | Thứ tự thực thi, điều phối worker waves và các human gate. **Không phải cạnh nhân quả**. | `depends_on` (Task ➔ Task) |
| **3. Code Graph (Mã nguồn)** | `graphify-out/` (`graphify`) | Cấu trúc cú pháp code cục bộ (AST), lời gọi hàm, class, module. | `calls`, `defines`, `imports` |

---

## 2. Mô hình thực thể & Bộ cạnh có kiểm soát

### 8 Loại Thực Thể (Node Types)
1. `Run`: Thực thể đại diện cho một lần chạy pipeline (`run:<id>`).
2. `Task`: Nhiệm vụ trong kế hoạch (`task:<id>`).
3. `Decision`: Quyết định kiến trúc, metric, threshold hoặc chính sách (`decision:<slug>`).
4. `Experiment`: Thí nghiệm, probe, benchmark, ablation (`exp:<slug>`).
5. `Artifact`: Dữ liệu hoặc model có version tag (`artifact:dataset:<ver>`, `artifact:model:<ver>`, `artifact:eval:<id>`, `artifact:report:<id>`).
6. `Incident`: Sự cố, lỗi môi trường, rò rỉ dữ liệu, vi phạm sandbox (`incident:<slug>`).
7. `Person`: Tác nhân con người hoặc role agent (`person:<role_or_user>`).
8. `Policy`: Ràng buộc, quy tắc vận hành, ngân sách (`policy:<slug>`).

### 8 Loại Cạnh Có Kiểu (Controlled Edge Types) & Ràng Buộc Đầu/Cuối
Mỗi cạnh quan hệ đều được kiểm tra hợp lệ kiểu đầu (source) và cuối (target):

| Loại cạnh | Ý nghĩa ngữ nghĩa | Kiểu nguồn (Source) | Kiểu đích (Target) |
|---|---|---|---|
| `depends_on` | Phụ thuộc tiền điều kiện thực thi | Task, Decision, Run, Policy | Task, Decision, Artifact, Policy, Run |
| `uses` | Sử dụng tài nguyên hoặc quy tắc | Task, Experiment, Decision, Artifact, Run | Artifact, Policy, Task |
| `evaluated_on` | Đánh giá trên tập dữ liệu/artifact | Experiment, Artifact, Task | Artifact |
| `decided_by` | Tác nhân đưa ra quyết định | Decision, Policy | Person |
| `approved_by` | Người/Gate phê duyệt quyết định | Decision, Policy, Task, Run | Person |
| `supersedes` | Thay thế phiên bản hoặc quyết định cũ | Decision, Artifact, Policy, Experiment | Decision, Artifact, Policy, Experiment |
| `caused` | Nguyên nhân trực tiếp dẫn tới hệ quả/sự cố | Incident, Decision, Experiment, Task | Incident, Decision, Experiment, Task |
| `evidenced_by` | Căn cứ thực nghiệm / báo cáo minh chứng | Decision, Incident, Experiment, Policy | Experiment, Artifact, Incident |

---

## 3. Quản lý hiệu lực thời gian (Temporal Validity & As-of)

Dữ liệu trong `runs/<id>/knowledge/` là **append-only** (chỉ thêm mới, không ghi đè xóa lịch sử):
- `valid_from`: Thời điểm bắt đầu có hiệu lực trong thực tế.
- `valid_to`: Thời điểm hết hiệu lực hoặc bị thay thế (`null` nghĩa là đang còn hiệu lực).
- `recorded_at`: Thời điểm sự kiện được ghi vào đồ thị.
- `source_ref`: Tham chiếu nguồn gốc (file path, dòng code, journal timestamp).

### Nguyên tắc thay thế (Superseding)
Khi một quyết định thay đổi (ví dụ: đổi metric break_time từ thô sang chuẩn hóa phút):
1. **Giữ nguyên** bản ghi quyết định cũ trong `entities.jsonl`.
2. Ghi bản ghi quyết định mới vào `entities.jsonl`.
3. Ghi cạnh `supersedes` từ quyết định mới sang quyết định cũ vào `edges.jsonl`.
4. Khi truy vấn tại mốc quá khứ (`--as-of <thời_điểm_cũ>`), quyết định cũ hiển thị là `ACTIVE`.
5. Khi truy vấn tại hiện tại hoặc tương lai, quyết định cũ hiển thị là `SUPERSEDED` bởi quyết định mới.

---

## 4. Khi nào bắt buộc Ghi & Truy vấn

### Khi nào Ghi (Write Rules)
- **Tự động qua `notebook.py log`**:
  - Ghi `--type decision|gate` ➔ Tự động sinh node `Decision` và cạnh `decided_by` nối tới tác giả.
  - Ghi `--type experiment|research|insight` ➔ Tự động sinh node `Experiment` và cạnh `evaluated_on`/`evidenced_by` từ `--refs`.
  - Ghi `--type error` ➔ Tự động sinh node `Incident`.
  - Có thể truyền bổ sung `--kg-edges "supersedes:decision:...,evidenced_by:exp:..."`.
- **Tự động qua `settle_task.py`**:
  - Khi một task hoàn tất, tự động ghi node `Task` và các cạnh `depends_on` từ `deps` trong `plan.json`.
- **Thủ công qua CLI**:
  - Dùng `python scripts/kg.py add-entity` và `add-edge` khi cần bổ sung quan hệ nghiệp vụ phức tạp.

### Khi nào Bắt buộc Truy vấn (Mandatory Query Points)
1. **Trong `ai-pipeline-status`**:
   - Trước khi báo cáo trạng thái, agent assessor BẮT BUỘC chạy:
     ```sh
     python scripts/kg.py timeline <run_dir> --as-of "<current_time>"
     ```
     để liệt kê chính xác các quyết định và chính sách nào đang có hiệu lực.
2. **Trong `ai-pipeline-planning` (Model Debate / Architecture)**:
   - Trước khi đề xuất thay đổi mô hình hoặc chiến lược, BẮT BUỘC chạy:
     ```sh
     python scripts/kg.py explain <run_dir> <decision_id>
     python scripts/kg.py path <run_dir> <node_A> <node_B>
     ```
     để xem lại vì sao kiến trúc trước đó được chọn và bằng chứng thực nghiệm nào đã hỗ trợ nó.
3. **Trong `ai-pipeline-diagnose` (Chẩn đoán thành phần yếu)**:
   - Khi phát hiện một module dưới mục tiêu, BẮT BUỘC gọi:
     ```sh
     python scripts/kg.py neighbors <run_dir> <module_or_task_id> --direction both
     ```
     để lần theo tập dữ liệu đánh giá, các sự cố liên quan và các giả định thiết kế ban đầu.

---

## 5. Hướng dẫn CLI `scripts/kg.py`

```sh
# Khởi tạo thư mục knowledge/
python scripts/kg.py init runs/<id>

# Thêm thực thể thủ công
python scripts/kg.py add-entity runs/<id> --id decision:norm-break --type Decision --title "Chuẩn hóa phút" --body "So khớp theo số phút"

# Thêm cạnh có kiểu
python scripts/kg.py add-edge runs/<id> --source decision:norm-break --target decision:raw-break --type supersedes --valid-from "2026-10-03 20:58"

# Kiểm tra tính toàn vẹn (enum, đầu/cuối, thứ tự thời gian)
python scripts/kg.py validate runs/<id>

# Xem các nút láng giềng kề
python scripts/kg.py neighbors runs/<id> decision:norm-break --direction both

# Tìm đường đi đa chặng giữa 2 thực thể
python scripts/kg.py path runs/<id> task:RT decision:remove-host-packages

# Giải thích toàn cảnh ngữ cảnh và nguồn gốc thực thể
python scripts/kg.py explain runs/<id> decision:norm-break --as-of "2026-10-04 12:00"

# Xem dòng thời gian thực thể và cạnh
python scripts/kg.py timeline runs/<id> --entity-type Decision

# Backfill tri thức từ run cũ sang graph mẫu
python scripts/kg.py backfill runs/timesheet-ocr --out-dir examples/kg.sample
```

---

## 6. Trực quan hóa trong Obsidian Vault

Khi chạy lệnh:
```sh
python scripts/obsidian_vault.py --run runs/<id> --out vault
```
Hệ thống tự động:
1. Đọc `knowledge/entities.jsonl` và `edges.jsonl`.
2. Tạo ghi chú riêng cho từng thực thể trong `docs/runs/<id>/knowledge/entities/`.
3. Render quan hệ có kiểu trực tiếp trong YAML Frontmatter (`relations:`) và danh sách liên kết có nhãn trong thân bài (`- **decided_by**: [[...]]`).
4. Tạo trang tổng quan `docs/runs/<id>/knowledge/KNOWLEDGE_GRAPH.md` liên kết toàn bộ sơ đồ tri thức để mở trong Obsidian Graph view.
