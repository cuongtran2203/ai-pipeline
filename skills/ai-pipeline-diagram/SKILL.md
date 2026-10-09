---
name: ai-pipeline-diagram
description: Ve so do Excalidraw cho report pipeline va kien truc mo hinh - khi can lam report hoac tai lieu ve pipeline, kien truc model, hoac nguoi dung yeu cau so do.
---

# Vẽ sơ đồ cho report (Excalidraw + SVG)

Dùng khi: report/tài liệu nói về **pipeline** (luồng task, luồng dữ liệu) hoặc **kiến trúc mô hình**
(backbone/neck/head/loss), hoặc người dùng yêu cầu sơ đồ. Công cụ duy nhất:
`scripts/diagram.py` (stdlib, không mạng, không cài gì).

## Quy trình

1. **Xác định ý nghĩa sơ đồ** — pipeline (task DAG) hay kiến trúc model (khối nối tiếp/nhánh)?
   Mỗi sơ đồ một ý. Không quá ~25 node/sơ đồ; vượt thì công cụ tự tách sơ đồ con.
2. **Viết spec JSON** — xem `templates/diagram.spec.example.json` (pipeline OCR) hoặc
   `templates/model_spec.example.json` (classifier chữ số):
   - pipeline: `{title, direction LR|TB, lanes?, nodes:[{id,label,kind,lane?,note?}], edges:[{from,to,label?,style?}], legend?}`
   - model: `{model, version?, blocks:[{id,label,part,tensor?,repeat?}], edges:[...]}`,
     part trong `input|backbone|neck|head|loss|postprocess|output`; tensor ghi ở nhãn phụ,
     khối lặp ghi `repeat` (hiển thị `×N`).
3. **Render** — một trong:
   - `python scripts/diagram.py render spec.json --out <dir> --name ten`
   - `python scripts/diagram.py from-plan plan.json --out <dir> --name pipeline`
     (màu theo role/nhóm từ `roles/registry.json`, gate G1/G2/G3 là cổng nét đứt, nhóm theo phase)
   - `python scripts/diagram.py from-model model_spec.json --out <dir> --name kien-truc`
   - Mỗi lệnh ra 3 định dạng: `.excalidraw` (mở/sửa tay), `.svg` (nhúng report),
     `.excalidraw.md` (Obsidian). Lọc bằng `--formats excalidraw,svg,obsidian`.
4. **Validate bắt buộc trước khi nhúng**: `python scripts/diagram.py validate file.excalidraw`
   (exit 1 + danh sách lỗi khi sai). Chỉ nhúng file đã `VALID`.
5. **Nhúng vào report** — `report.html` nhúng SVG nội tuyến, `report.md` chèn liên kết file
   (xem `skills/ai-pipeline-report/SKILL.md`); file sơ đồ đặt trong
   `runs/<id>/reports/round-NN-<slug>/diagrams/`.

## Khi nào Mermaid, khi nào Excalidraw

- **Mermaid** (khối ```mermaid trong md): README, ghi chú nhanh, sơ đồ nhẹ không cần sửa tay.
- **Excalidraw** (skill này): report cuối vòng, kiến trúc mô hình, sơ đồ pipeline trình G2 —
  nơi cần màu semantic, mũi tên vuông góc chuẩn, và khả năng mở ra chỉnh tay.

## Mở file thế nào

- `.excalidraw`: mở bằng extension VS Code `Pomdtr.excalidraw-editor` hoặc web excalidraw.com
  (kéo-thả file vào). Sửa tay xong nên chạy lại `validate` để chắc binding còn nguyên.
- `.excalidraw.md`: mở trong Obsidian đã cài plugin Excalidraw (tác giả Zsolt Viczian):
  gồm khối YAML, bảng `# Text Elements` và khối `# Drawing` JSON chưa nén.

## Quy tắc cứng (bắt buộc)

- **KHÔNG dùng diamond** cho quyết định/cổng (G1/G2/G3): dùng chữ nhật bo góc nét đứt,
  `strokeWidth` 2. `validate` từ chối mọi element diamond.
- **Nhãn bắt buộc 2 element**: shape giữ `boundElements` trỏ text, text giữ `containerId`
  trỏ shape (ràng buộc hai chiều). Mọi chữ trong sơ đồ đều phải có container.
- **Mũi tên vuông góc**: `elbowed:true, roughness:0, roundness:null`; điểm đầu/cuối nằm
  ĐÚNG trên mép hộp tại điểm giữa cạnh (top/bottom/left/right), dung sai 1px.
- **Bảng màu semantic** (`nền/viền`): API/entrypoint `#e7f5ff/#1971c2`,
  service/logic `#ebfbee/#2f9e44`, DB/storage `#fff9db/#f08c00`,
  queue/cache/async `#f3f0ff/#7950f2`, AI model/inference `#fff0f6/#d6336c`,
  security/auth/gate `#ffe3e3/#e03131`; mở rộng cho pipeline AI: data/ETL `#e6fcf5/#0c8599`,
  training job (nhóm AI), evaluation/metric (nhóm entrypoint), human gate (nhóm gate, nét đứt),
  artifact/report (nhóm storage), external data `#f1f3f5/#868e96`.
- Chữ tiếng Việt có dấu phải nguyên vẹn trong SVG (UTF-8, escape XML, không font ngoài).

## Các bẫy thường gặp

- Vẽ diamond cho "đạt mục tiêu?" → `validate` báo lỗi; đổi kind thành `decision`/`human-gate`.
- Text mất `containerId` sau khi sửa tay → chạy `validate`, render lại từ spec thay vì vá JSON.
- Mũi tên lệch mép hộp dù chỉ 2px → `validate` báo lỗi; render lại từ spec (tọa độ do máy tính).
- Sơ đồ >25 node → công cụ tự tách `_p1, _p2...`, cạnh liên phần liệt kê ở legend + console.
- Mở `.excalidraw.md` thấy JSON thô thay vì canvas → Obsidian thiếu/sai plugin, hoặc khối
  `# Drawing` bị sửa tay (render lại, không sửa khối đó).

## Tùy chọn cần người duyệt (KHÔNG làm tự động)

Theo quy tắc sandbox, hai việc sau cần `ask` người trước và KHÔNG thuộc luồng mặc định:

- Render PNG: `npx -y @excalidraw/cli render file.excalidraw --out file.png` (cần Node/npm + mạng).
- Công thức LaTeX / biểu đồ loss bằng matplotlib (cần cài gói, chỉ chạy trong container).

## Giới hạn chưa kiểm chứng

- Chưa mở file thật trong Excalidraw/Obsidian để đối chiếu hiển thị (chỉ kiểm bằng
  `validate` + đọc SVG). Khối `# Drawing` dùng JSON chưa nén; plugin gốc còn chấp nhận cả
  khối nén `compressed-json` mà công cụ này không sinh ra.
- Mũi tên `elbowed` hiển thị đúng trong engine Excalidraw; SVG tự vẽ đường gấp khúc tương đương.

## Nguồn ý tưởng

Ý tưởng và quy ước (màu semantic, nhãn 2 element, mũi tên vuông góc, tương thích Obsidian)
rút từ tài liệu `EXCALIDRAW_SKILL_GUIDE.md` của dự án và ba nguồn gốc:
`coleam00/excalidraw-diagram-skill`, `ooiyeefei/ccc` (skill excalidraw),
`zsviczian/obsidian-excalidraw-plugin`. Viết lại bằng lời của ta, không sao chép nguyên văn;
thư mục skill gốc `.agents/skills/excalidraw/` mà tài liệu nhắc không tồn tại trên máy này.
