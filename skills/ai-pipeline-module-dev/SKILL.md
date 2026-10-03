---
name: ai-pipeline-module-dev
description: Process for developing one AI model/module inside the pipeline workflow - understand the problem, version datasets, train on GPU server after Gate G3, evaluate on real data, and emit md+html reports. Used by module-dev workers.
---

# Module development (per module)

1. **Understand**: read this module's section of architecture.md, the targets, data_analysis.md. Re-check existing solutions/public data if architecture left it open.
2. **Dataset**: build/convert; assign `ds-vN`; record size, train/val/test split, mixing ratios and source in `dataset_card.md`. The real eval set is separate from synthetic. Without real data, justify how the synthetic distribution covers real.
3. **Train** — requires G3 info (server, GPU type, CUDA version, framework, or open-source link). If missing, use the Orca `ask` command; never invent. Record command, config, seed, checkpoint `model-vX.Y`.
4. **Evaluate**: on the real val set when available (≥50 labeled samples) plus synthetic val; compare with baseline on the same set. Write `eval.json` (format: `examples/eval.sample.json`): per-component tables, clustered errors with concrete examples, cause marked confirmed vs hypothesis.
5. **Report**: `python scripts/render_report.py eval.json --out-dir <module dir>` → `report.md` (Vietnamese, 3 sections) + `report.html`. Both are mandatory after every evaluation.
6. **Iterate** only within the scope given; larger changes → escalate to the coordinator via `ask`.
7. **Giữ đơn giản**: trước khi đổi kiến trúc, trả lời (a) phân bố train vs test có lệch không? (b) lỗi trên test nói gì về cách training? Sửa từ đó.

Done = checkpoint + dataset card + eval.json + both reports in the module directory.
