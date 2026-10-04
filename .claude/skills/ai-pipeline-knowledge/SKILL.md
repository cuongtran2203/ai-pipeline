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

### Một API ghi duy nhất (Single Write API)
Mọi writer (kg.py, settle_task.py, notebook.py, các script sau này) **chỉ ghi qua API Python của `scripts/kg.py`**, không tự append `entities.jsonl`/`edges.jsonl`:
```python
import kg
kg.upsert_entity(run_dir, id, type, title, body="", properties={}, created_at=None)
kg.upsert_artifact_ref(run_dir, ref, created_at=None, kind=None)   # ref giữ đường dẫn tương đối gốc dự án
kg.add_edge_checked(run_dir, source, target, type, valid_from=None, ..., allow_dangling=False)
```
API validate enum, tồn tại node, kiểu đầu–cuối, thứ tự thời gian (`valid_from <= valid_to`) và `confidence`; sai thì ném `kg.KgError` rõ ràng. Ghi lặp cùng `(source, target, type)` là idempotent.

**Khóa và tính giao dịch (RV3 wave-3):**
- Mọi ghi entity/edge chạy dưới **khóa mức đồ thị** `knowledge/graph.lock`; thao tác check-then-append không bị tiến trình khác chen vào, hai worker upsert/cạnh trùng song song vẫn idempotent theo khóa (không sinh bản trùng).
- `allow_dangling=True` **CHỈ** nới trường hợp endpoint **THIẾU** (backfill theo thứ tự). Endpoint **đã tồn tại nhưng sai kiểu LUÔN bị từ chối** — không được lấy `allow_dangling` để đưa cạnh sai kiểu vào đồ thị.
- `init`/`backfill` ghi atomic: dựng vào một graph tạm rồi swap từng file bằng `os.replace`, không để lại file nửa chừng.
- Ghi file trạng thái chung (`done.json`, `notebooklm.json`, pending...) qua `scripts/statefile.py` (`update_json`/`append_jsonl`). Không lồng khóa: commit từng file rồi ghi pending sync, KG và notebook không giữ khóa chồng lên nhau.

> **Lưu ý output của task:** không có loại cạnh nào trong 8 loại diễn đạt quan hệ "task sinh ra artifact". `uses` chỉ mang nghĩa task **dùng** artifact. Vì vậy `settle_task.py` **không phát cạnh output** mà ghi danh sách `outputs` (path kèm version tag) vào `properties` của thực thể `Task`. Quan hệ `produced` là **quyết định thiết kế đang chờ người dùng duyệt** (xem `runs/ai-pipeline-v2/artifacts/FE/README.md`); không tự thêm loại cạnh mới khi chưa duyệt.

### ID ổn định cho notebook
`notebook.py log` gắn khóa nguồn ổn định `run#<ordinal>-<hash8>-<uuid8>` vào trường `id` của mục journal. Ordinal được cấp **dưới khóa journal** nên hai worker đồng thời không đụng ordinal và không mất mục; UUID khiến hai mục giống hệt trong cùng một phút vẫn thành hai node khác nhau. ID thực thể KG suy từ khóa này nên **tiêu đề trùng / nội dung trùng không đụng ID**. `refs` giữ **đường dẫn chuẩn hoá theo gốc dự án** (`runs/x/../y/a.json` == `runs/y/a.json`, POSIX separators, kèm `version` nếu nhận ra); ref nằm **ngoài gốc dự án bị từ chối/đánh dấu**, không sinh `../../..`.

Mục journal **cũ không có `id`** được cấp khóa nguồn ổn định **một lần** khi migration (kèm backup `journal.jsonl.bak-<ts>`), lưu thẳng vào `journal.jsonl`; về sau không tái tính theo vị trí, nên hai mục giống hệt vẫn là hai node. `journal.md`/`insights.md` được rebuild atomic; snapshot journal được **chụp bên trong render lock** nên rebuild cũ không thể ghi đè rebuild mới (`journal.md` luôn đủ mọi mục của `journal.jsonl`).

### Pending sync & reconcile
Nếu đồng bộ KG lỗi, `settle_task.py` ghi `knowledge/sync_pending.json`, `notebook.py` ghi `notebook/sync_pending.json` — **không** làm hỏng `done.json`/journal. Chạy lại bằng `settle_task.py <run_dir> --reconcile` hoặc `notebook.py reconcile <run_dir>`. Reconcile **chỉ xoá key đã sync thành công khi giá trị hiện tại vẫn khớp giá trị đã đọc**, nên key do writer khác thêm/đổi trong lúc sync không bị xoá mất.

### Dọn cạnh legacy (`quarantine`)
Khi đồ thị còn cạnh không hợp lệ do writer cũ (ví dụ `Artifact -> Task evidenced_by`), dùng:
```sh
python scripts/kg.py quarantine runs/<id>            # dry-run: liệt kê cạnh sai + lý do, KHÔNG sửa
python scripts/kg.py quarantine runs/<id> --apply    # backup entities/edges (tên độc nhất) rồi chuyển cạnh sai
```
`--apply` **chỉ** chuyển các cạnh không hợp lệ sang `knowledge/edges.quarantine.jsonl` (giữ nguyên nội dung cạnh + `reason` + `quarantined_at`), ghi lại `edges.jsonl` atomic, sau đó `kg.py validate` phải VALID. Lệnh idempotent: chạy lại khi không còn cạnh sai thì không sửa gì. Cạnh hợp lệ không bị đụng; không tự sửa/xoá thực thể.

### Đọc nghiêm (strict) để không che mất bản ghi
`statefile.read_jsonl(path, strict=True)` chỉ cho phép **dòng cuối** bị ghi dở; dòng hỏng/trống ở **giữa file** → `StateCorrupt`. `kg.py` (`read_entities`/`read_edges`) và `notebook.py` (`journal.jsonl`) dùng strict; mặc định `strict=False` giữ tương thích cũ. `statefile.update_json` coi file đã tồn tại mà RỖNG là `StateCorrupt` (chỉ file không tồn tại mới dùng default).

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

# Backfill theo thứ tự khi endpoint còn thiếu (chỉ nới THIẾU endpoint; sai kiểu vẫn bị từ chối)
python scripts/kg.py add-edge runs/<id> --source task:T --target artifact:runs/<id>/x.json --type uses --allow-dangling

# Retry đồng bộ KG còn pending sau khi done.json đã commit
python scripts/settle_task.py runs/<id> --reconcile
python scripts/notebook.py reconcile runs/<id>

# Kiểm tra tính toàn vẹn (enum, đầu/cuối, thứ tự thời gian)
python scripts/kg.py validate runs/<id>

# Báo cáo (chỉ đọc, KHÔNG tự sửa) cạnh/thực thể sai kiểu hoặc lơ lửng do writer cũ
python scripts/kg.py report runs/<id>

# Dọn cạnh legacy: dry-run rồi --apply (backup + chuyển sang edges.quarantine.jsonl)
python scripts/kg.py quarantine runs/<id>
python scripts/kg.py quarantine runs/<id> --apply

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
