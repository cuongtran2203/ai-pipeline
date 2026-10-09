---
name: ai-pipeline-module-dev
description: Process for developing one AI model/module inside the pipeline workflow - understand the problem, version datasets, train on GPU server after Gate G3, evaluate on real data, and emit md+html reports. Used by module-dev workers.
---

# Module development (per module)

1. **Understand**: read this module's section of architecture.md, the targets, data_analysis.md. Re-check existing solutions/public data if architecture left it open.
0. **Sandbox**: chạy mọi thứ trong container (`ai-pipeline-sandbox`); không cài thư viện khi chưa được phép — `ask`.
2. **Dataset**: build/convert; assign `ds-vN`; record size, split strategy (`random | group | temporal | rolling-origin` + mốc cắt/embargo), as-of feature definitions, mixing ratios and source in `dataset_card.md`, kèm **leakage audit** (trùng entity/thời gian giữa fold, feature nhìn tương lai). The real eval set is separate from synthetic. Cỡ mẫu eval theo đơn vị/metric/base rate đã chốt ở intake (mặc định gợi ý: ≥100 đơn vị; lớp hiếm ≥50 dương tính/lớp) — không dùng ngưỡng cứng chung. Without real data, justify how the synthetic distribution covers real.
3. **Train** — chỉ task cần G3 (`mode: train` hoặc `resources.compute: gpu`): requires G3 info (server, GPU type, CUDA version, framework, or open-source link). Task CPU-only (`evaluate-only | retrieve-only | inference-service | monitor` với `compute: cpu`) bỏ qua bước này (chạy CPU/container thường, không cần G3). If missing, use the Herdr `ask` command; never invent. Record command, config, seed, checkpoint `model-vX.Y`.
4. **Evaluate**: theo `eval_contract` trong spec (đơn vị, split, metric + hướng tốt, slice/horizon, scorer human|model|rule, CI) trên tập eval đã chốt; compare with baseline on the same set. Write `eval.json` (schema: `schemas/eval.schema.json`; ví dụ OCR: `examples/eval.sample.json`; ví dụ phi OCR: `examples/rag-chatbot|fraud-tabular|forecast-timeseries/eval.json`): per-scope metric tables, clustered errors with concrete examples, cause marked confirmed vs hypothesis.
5. **Report**: `python scripts/render_report.py eval.json --out-dir <module dir>` → `report.md` (ngôn ngữ theo `eval.json` `lang` / `plan.report_lang`, mặc định vi; đúng 3 phần) + `report.html`. Both are mandatory after every evaluation/experiment round (also copy into `runs/<id>/reports/round-NN-<slug>/`); the md states how the experiment was run, which problem it solved, and the detailed results.
6. **Log**: each train/eval/probe/ablation → `scripts/notebook.py log … --type experiment` (hypothesis, setup, metrics, conclusion), including negative results.
7. **Iterate** only within the scope given. Every iteration states a hypothesis, `predicted_gain` and how it is measured; choose checkpoints/hyper-parameters on **val**, score **test once** at the end. Log rounds into `ceiling.json` (C2) and stop per `ai-pipeline-feasibility` rules; larger changes → escalate to the coordinator via `ask`.
8. **Giữ đơn giản**: trước khi đổi kiến trúc, trả lời (a) phân bố train vs test có lệch không? (b) lỗi trên test nói gì về cách training? Sửa từ đó.

Done = checkpoint + dataset card + eval.json + both reports in the module directory.

## Hợp đồng launch huấn luyện (train/eval)

Đầy đủ ở `skills/ai-pipeline-sandbox/SKILL.md` §Hợp đồng launch; tóm tắt áp dụng cho module:

1. Train/eval chạy từ **snapshot commit bất biến**; ghi **commit SHA** vào `eval.json`, `report.md`/`report.html` và sổ. Thiếu SHA ⇒ lần chạy không tính là bằng chứng.
2. **Lệnh cố định ghi trước khi chạy** (launcher/config đã commit); không chạy tay biến thể ngoài launcher — biến thể nào cũng phải vào config rồi commit lại.
3. **Log + exit code là bằng chứng duy nhất**: đọc log để kết luận, không tin `status`/trí nhớ; lưu log vào run dir/artifact.
4. **So sánh công bằng:** mỗi biến thể chỉ đổi **một yếu tố** trên nhánh tốt nhất hiện tại; giữ nguyên seed/split/epoch/container.
5. Sau **~3 fail liên tiếp** trên một nhánh → dừng, chẩn đoán (`ai-pipeline-diagnose`), không thử mò.
6. Mỗi run ghi `python scripts/notebook.py log <run_dir> --type experiment ...` kèm commit SHA + lệnh + exit code + số đo chính.

## Seal test (nhãn test)
- Data analyst tạo `runs/<id>/eval_manifest.json` bằng `scripts/seal.py manifest` (chỉ hash + đường dẫn tương đối, KHÔNG chứa nhãn); đặt nhãn ngoài checkout bằng `--labels-dir`/`SEAL_LABELS_ROOT`.
- module-dev chỉ train/val: cấm đọc/ghi log mang nhãn test, không tự mở nhãn test.
- Integrator: khoá recipe (`seal.py lock`) rồi `seal.py grant --task <T>` đúng MỘT lần (role lấy từ plan.json); sửa threshold sau lock bị từ chối; mở lại gắn `exploratory` (không còn blind final); kiểm audit bằng `seal.py verify-audit`. Chấm một lượt, công bố metric kèm n/slice/CI.
- seal chỉ ở mức mã, không phải biên bảo vệ filesystem (xem `scripts/seal.md`).