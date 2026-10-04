# scripts/statefile.py — ghi trạng thái giao dịch (JSON/JSONL) cho nhiều tiến trình

Thư viện stdlib sửa lỗi P1 "ghi trạng thái không giao dịch" trong
`runs/ai-pipeline-v2/reviews/debate_v2_improvements.md`. KHÔNG tự chạy; là nền để tích hợp
vào các script ở wave sau.

## API
- `read_json(path, default=None)`: đọc JSON, chịu BOM UTF-8. Thiếu file hoặc JSON hỏng → `default`.
- `update_json(path, fn, default=None)`: khóa → đọc → `fn(dữ_liệu_cũ)` → ghi file tạm + `os.replace`
  (atomic) → trả dữ liệu mới. **File đã tồn tại mà RỖNG, hoặc JSON hỏng → `StateCorrupt`, KHÔNG ghi đè**
  (chỉ file *không tồn tại* mới là legacy-missing và dùng `default`).
- `append_jsonl(path, obj)`: khóa → ghi 1 dòng JSON + `flush`/`fsync`. Mỗi dòng là 1 JSON hợp lệ.
- `read_jsonl(path, strict=False)`: đọc JSONL.
  - `strict=False` (mặc định, tương thích cũ): bỏ qua dòng trống và dòng không parse được (kể cả dòng cuối ghi dở).
  - `strict=True`: chỉ cho phép **dòng cuối** bị ghi dở (không có newline kết thúc); dòng hỏng/trống ở
    **giữa file** → `StateCorrupt`. Dùng cho reader audit/KG/journal để không che mất bản ghi.
- `file_lock(path, timeout=30.0)`: context manager giữ khóa độc quyền cho thao tác nhiều bước;
  quá hạn → `LockTimeout`.

## Bảo đảm
- Nhiều tiến trình cùng ghi: không mất bản ghi, không ghi đè lẫn nhau (khóa liên tiến trình).
- File chính luôn hợp lệ: ghi ra file tạm cùng thư mục + fsync rồi `os.replace`.
- Khóa do OS quản lý (`msvcrt.locking` trên Windows, `fcntl.flock` trên POSIX): tiến trình chết
  giữa chừng tự nhả khóa → không khóa mồ côi, không deadlock.
- `read_jsonl` (mặc định) bỏ dòng cuối bị cắt dở khi crash lúc append; `strict=True` báo lỗi nếu
  bản ghi bị hỏng ở giữa thay vì bỏ qua im lặng.

## Giới hạn
- Không phải CSDL: không index/truy vấn, đọc-ghi toàn bộ file → chỉ hợp file nhỏ (state điều phối).
- Trên Windows, `os.replace` có thể vướng nếu tiến trình khác đang mở file đích; đã có retry ngắn.
- Khóa đặt cạnh file: `<path>.lock` (file rỗng).

## Tích hợp
- `scripts/settle_task.py`: đọc/ghi `done.json` bằng `update_json` để không mất done khi nhiều coordinator.
- `scripts/plan_to_orca.py`: `started.json` và `usage.json` (quota theo từng worker) qua `update_json`.
- `scripts/notebook.py`: `append_jsonl` cho `journal.jsonl`, rebuild Markdown chụp journal **bên trong**
  `file_lock` (render guard) để rebuild cũ không ghi đè rebuild mới; journal đọc bằng `read_jsonl(strict=True)`.
- `scripts/kg.py`: `read_entities`/`read_edges` đọc `entities.jsonl`/`edges.jsonl` bằng `read_jsonl(strict=True)`.
- `scripts/autonomy.py` (audit) và `scripts/seal.py` (seal audit) nên đọc audit bằng `read_jsonl(strict=True)`;
  hiện nằm ngoài phạm vi wave 4 của GB.

## Kiểm chứng
`python -m unittest discover -s tests -v` (xem `tests/test_statefile.py`: race multiprocessing,
crash giữa temp/replace, BOM UTF-8, dòng cuối ghi dở).
