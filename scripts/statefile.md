# scripts/statefile.py — ghi trạng thái giao dịch (JSON/JSONL) cho nhiều tiến trình

Thư viện stdlib sửa lỗi P1 "ghi trạng thái không giao dịch" trong
`runs/ai-pipeline-v2/reviews/debate_v2_improvements.md`. KHÔNG tự chạy; là nền để tích hợp
vào các script ở wave sau.

## API
- `read_json(path, default=None)`: đọc JSON, chịu BOM UTF-8. Thiếu file hoặc JSON hỏng → `default`.
- `update_json(path, fn, default=None)`: khóa → đọc → `fn(dữ_liệu_cũ)` → ghi file tạm + `os.replace`
  (atomic) → trả dữ liệu mới.
- `append_jsonl(path, obj)`: khóa → ghi 1 dòng JSON + `flush`/`fsync`. Mỗi dòng là 1 JSON hợp lệ.
- `read_jsonl(path)`: đọc JSONL; bỏ qua dòng trống và dòng không parse được (kể cả dòng cuối ghi dở).
- `file_lock(path, timeout=30.0)`: context manager giữ khóa độc quyền cho thao tác nhiều bước;
  quá hạn → `LockTimeout`.

## Bảo đảm
- Nhiều tiến trình cùng ghi: không mất bản ghi, không ghi đè lẫn nhau (khóa liên tiến trình).
- File chính luôn hợp lệ: ghi ra file tạm cùng thư mục + fsync rồi `os.replace`.
- Khóa do OS quản lý (`msvcrt.locking` trên Windows, `fcntl.flock` trên POSIX): tiến trình chết
  giữa chừng tự nhả khóa → không khóa mồ côi, không deadlock.
- `read_jsonl` bỏ dòng cuối bị cắt dở khi crash lúc append.

## Giới hạn
- Không phải CSDL: không index/truy vấn, đọc-ghi toàn bộ file → chỉ hợp file nhỏ (state điều phối).
- Trên Windows, `os.replace` có thể vướng nếu tiến trình khác đang mở file đích; đã có retry ngắn.
- Khóa đặt cạnh file: `<path>.lock` (file rỗng).

## Tích hợp ở wave sau
- `scripts/settle_task.py`: đọc/ghi `done.json` bằng `update_json` để không mất done khi nhiều coordinator.
- `scripts/plan_to_orca.py`: `started.json` và `usage.json` (quota theo từng worker) qua `update_json`.
- `scripts/notebook.py`: `append_jsonl` cho `journal.jsonl`, rebuild Markdown trong cùng `file_lock`.
- `scripts/autonomy.py`: `append_audit` dùng `append_jsonl` để audit append-only không chen dòng.

## Kiểm chứng
`python -m unittest discover -s tests -v` (xem `tests/test_statefile.py`: race multiprocessing,
crash giữa temp/replace, BOM UTF-8, dòng cuối ghi dở).
