# scripts/seal.py — seal nhãn test tối thiểu

Chặn việc chọn threshold/xem nhãn test trong lúc phát triển (P1 trong
`runs/ai-pipeline-v2/reviews/debate_v2_improvements.md`; thiết kế ở mục
"Seal nhãn test tối thiểu cho wave sau" của `review_wave_fix_p1.md`).
Không mã hoá/KMS riêng: kiểm soát bằng manifest hash + quyền theo role + audit append-only.

## Quy trình 5 bước
1. Data analyst chốt tập test, tạo manifest (KHÔNG chứa nhãn):
   `python scripts/seal.py manifest <run_dir> --dataset-version ds-v1 --split-id test-v1 --ids-file <F> --labels-file <L>`
   → `runs/<id>/eval_manifest.json` (dataset version, split id, n mẫu, SHA-256 danh sách ID + file nhãn, đường dẫn nhãn sealed).
2. Kiểm còn khớp: `python scripts/seal.py verify <run_dir>` (0 = khớp, khác 0 = lệch).
3. Khoá recipe TRƯỚC khi chấm: `python scripts/seal.py lock <run_dir> --model-version model-v1 --threshold-file <F>`
   → `runs/<id>/recipe_lock.json` kèm hash file ngưỡng.
4. Integrator xin nhãn test MỘT lần: `python scripts/seal.py grant <run_dir> --role integrator --task INT-1 --purpose final-eval`
   → in đường dẫn nhãn; chỉ `integrator`/`evaluator` được cấp, các role dev/module-dev/data-analyst bị từ chối.
5. Chấm một lượt, công bố metric kèm n/slice/CI. Mỗi lần grant/deny ghi append-only vào `runs/<id>/seal_audit.jsonl`
   (actor, UTC time, task/dispatch, manifest hash, recipe version, purpose, kết quả). Mở lần hai gắn cờ `exploratory` (không còn blind final).

## KHÔNG nên làm
- Không tự mã hoá/KMS riêng (quá phức tạp; hash + role + audit là đủ).
- Không đưa nhãn test vào prompt/log/Graphify/NotebookLM.
- Không tái dùng test để chỉnh threshold (mở lại chỉ là exploratory).
- Không áp dụng hồi tố cho run `timesheet-ocr` cũ.

## Kiểm chứng
`python -m unittest discover -s tests -p 'test_seal.py' -v` (9 test: dev/analyst bị từ chối,
integrator trước lock bị từ chối, sau lock được cấp, hash lệch bị từ chối, mở lần hai exploratory,
audit không mất khi grant đồng thời, manifest không chứa nhãn).