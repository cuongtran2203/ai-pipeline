# scripts/seal.py — seal nhãn test tối thiểu

Chặn chọn threshold/xem nhãn test trong lúc phát triển và siết theo RV4
(`runs/ai-pipeline-v2/reviews/review_wave3.md`). Không mã hoá/KMS riêng: kiểm soát ở mức MÃ
bằng manifest hash + role lấy từ plan + recipe hash + audit có chuỗi hash + anchor.
Đọc mục "Giới hạn còn lại" — đây KHÔNG phải biên bảo vệ ở mức filesystem.

## Quy trình 6 bước
1. Data analyst chốt tập test và tạo manifest (KHÔNG chứa nhãn):
   `python scripts/seal.py manifest <run_dir> --dataset-version ds-v1 --split-id test-v1 --ids-file <F> --labels-file <L> [--labels-dir <D>]`
   → `runs/<id>/eval_manifest.json` (dataset version, split id, n mẫu, SHA-256 danh sách ID + file nhãn,
   `labels_path` + `labels_root` dạng tương đối). Đặt nhãn NGOÀI checkout worker thấy bằng `--labels-dir`
   hoặc biến `SEAL_LABELS_ROOT` (khuyến nghị); file nhãn ngoài gốc dự án mà không khai root -> từ chối
   (không ghi đường dẫn tuyệt đối vào manifest).
2. Kiểm còn khớp: `python scripts/seal.py verify <run_dir>` (0 = khớp, khác 0 = lệch).
3. Khoá recipe TRƯỚC khi chấm: `python scripts/seal.py lock <run_dir> --model-version model-v1 --threshold-file <F>`
   → `runs/<id>/recipe_lock.json` kèm hash file ngưỡng. Kiểm tồn tại + ghi trong CÙNG một khoá: hai lock
   đồng thời chỉ MỘT thắng.
4. Integrator xin nhãn test MỘT lần: `python scripts/seal.py grant <run_dir> --task INT-1 --purpose final-eval [--role integrator]`
   → in đường dẫn đã resolve. Role lấy TỪ PLAN (`plan.json` trong run_dir hoặc `artifacts/*/plan.json`); chỉ
   `integrator`/`evaluator` được cấp; `--role` chỉ để kiểm chéo (khác role trong plan -> từ chối); task không
   có trong plan -> từ chối. Grant đối chiếu lại hash threshold (sửa threshold sau lock -> `recipe_changed`)
   và hash manifest/nhãn. Toàn bộ kiểm audit + quyết định + ghi audit nằm trong MỘT khoá riêng
   (`seal_audit.guard`): hai grant đồng thời chỉ MỘT cái blind-final, cái còn lại `exploratory`.
5. Kiểm audit toàn vẹn: `python scripts/seal.py verify-audit <run_dir>` (0 = toàn vẹn). Mỗi dòng
   `runs/<id>/seal_audit.jsonl` có `prev_hash` (hash dòng trước) + `hash`; anchor (`seal_audit.anchor.json`,
   cập nhật mỗi lần ghi) chống cắt cuối. Grant từ chối (`audit_not_intact`) khi audit bị xoá/sửa/cắt.
6. Chấm một lượt, công bố metric kèm n/slice/CI. Mở lần hai gắn cờ `exploratory` (không còn blind final).

## Giới hạn còn lại (KHÔNG phải biên bảo vệ)
- Worker cùng quyền filesystem vẫn đọc được file nhãn nếu biết đường dẫn: `seal.py grant` chỉ là API; đọc
  thẳng file không qua grant. Cần mount/ACL hoặc identity Orca ở mức runtime (quyết định của người dùng) —
  hash/audit trên cùng filesystem không thay ACL.
- Manifest/audit/anchor đều nằm trên cùng filesystem: người có quyền ghi có thể tính lại hash (không có chữ ký).
- Chưa tích hợp vào `plan_to_orca.py` nên chưa tự chặn worker đọc nhãn lúc chạy.
- Chưa kiểm trên network share/OneDrive; chưa dùng Windows ACL/identity.

## KHÔNG nên làm
- Không tự mã hoá/KMS riêng (quá phức tạp; hash + role + audit + anchor là đủ cho mức mã).
- Không đưa nhãn test vào prompt/log/Graphify/NotebookLM.
- Không tái dùng test để chỉnh threshold (mở lại chỉ là exploratory).
- Không áp dụng hồi tố cho run `timesheet-ocr` cũ.

## Kiểm chứng
`python -m unittest discover -s tests -p 'test_seal.py' -v` (21 test: role từ plan, task ngoài plan/không plan,
lock/grant atomic barrier đa tiến trình, threshold sửa sau lock, audit xoá/sửa/cắt + anchor, path nhãn ngoài gốc).
