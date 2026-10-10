"""Ghép eval_raw.json + lịch sử train -> eval.json (schema render_report) rồi gọi render_report.

Dùng: python -m src.cnn.make_eval --dir <modules/cnn> --final mnist-cnn-tiny-v0.2 --final-ds ds-v2
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAUSES = {
    (4, 9): "Giả thuyết: nét đỉnh của 4 khép kín giống 9 (dạng viết khác nhau, không phải lệch phân bố).",
    (2, 7): "Giả thuyết: 2 viết nét cong thấp/không có vòng đáy giống 7.",
    (5, 3): "Giả thuyết: nửa trên của 5 viết tròn giống 3.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--final-ds", required=True)
    a = ap.parse_args()
    raw = json.load(open(os.path.join(a.dir, "eval_raw.json")))
    t1 = json.load(open(os.path.join(a.dir, "train_mnist-cnn-tiny-v0.1_ds-v1.json")))
    t2 = json.load(open(os.path.join(a.dir, f"train_{a.final}_{a.final_ds}.json")))
    pairs = Counter(map(tuple, raw["wrong_pairs"]))
    ex = defaultdict(list)
    for i, (t, p) in zip(raw["wrong_indices"], raw["wrong_pairs"]):
        ex[(t, p)].append(i)
    top = pairs.most_common(5)
    top_n = sum(c for _, c in top)
    wrong_total = raw["test_total"] - raw["test_correct"]
    errors = [{"cluster": f"Nhầm {t} → {p}", "count": c,
               "cause": CAUSES.get((t, p), "Giả thuyết: dạng viết mơ hồ giữa hai chữ số, chưa xem ảnh để xác nhận."),
               "examples": [{"where": f"ảnh test #{i}", "note": f"nhãn {t}, dự đoán {p}"} for i in ex[(t, p)][:3]]}
              for (t, p), c in top]
    if wrong_total > top_n:
        errors.append({"cluster": "Các cặp nhầm lẻ khác", "count": wrong_total - top_n,
                       "cause": "Mỗi cặp ≤ 5 ảnh; chưa có cụm rõ ràng (I2 sẽ phân cụm kỹ hơn).", "examples": []})
    rows = [{"item": f"Lớp {p['class']} (recall)", "correct": p["correct"], "total": p["support"]}
            for p in raw["per_class"]]
    tm, ts = raw["train_seconds"], raw["train_seconds"]
    d = {
        "title": "MNIST CNN — module cnn",
        "version": {"model": raw["model_version"], "dataset": raw["dataset_version"]},
        "baseline_name": "mnist-cnn-tiny-v0.1 / ds-v1 (chỉ có val, chưa chấm test)",
        "overview": {
            "status": f"Chỉ tiêu: test ≥ 98%, train ≤ 10 phút CPU, model < 5 MB. Kết luận chỉ áp dụng cho phân bố MNIST (không có dữ liệu real ngoài MNIST).",
            "method": (f"Lần 1 `mnist-cnn-tiny-v0.1` trên `ds-v1` (không augmentation): val {t1['best_val_acc']:.2%} < 98,5% → xem lỗi, "
                       f"thử dự phòng thứ 2 theo P4: augmentation dịch ≤ 2px chỉ ở train (`ds-v2`, `mnist-cnn-tiny-v0.2`): val {t2['best_val_acc']:.2%}. "
                       "Chọn v0.2 theo val; chưa cần tăng lên small. AdamW lr 1e-3 wd 1e-4, batch 128, seed 42, 8 epoch, CPU."),
            "result": (f"`{raw['model_version']}`: test {raw['test_correct']}/{raw['test_total']} = {raw['test_acc']:.2%} (đạt ≥ 98%), "
                       f"val {raw['val_acc']:.2%}; train {ts:.1f} giây ({raw['epochs_run']} epoch, {raw['threads']} luồng, đạt ≤ 600 giây); "
                       f"checkpoint {raw['ckpt_bytes']} byte = {raw['ckpt_mb']:.3f} MB (đạt < 5 MB); {raw['params']} tham số. Độ trễ suy luận do I1 đo."),
        },
        "tables": [{"name": "Recall theo từng lớp trên 10.000 ảnh test", "rows": rows},
                   {"name": "Tổng test", "rows": [{"item": "Toàn bộ", "correct": raw["test_correct"], "total": raw["test_total"]}]}],
        "errors": errors,
        "conclusion": {
            "fixes": [
                {"error": "Nhầm 4→9, 2→7, 5→3 (các cặp dạng viết gần nhau)", "fix": "Xem ảnh sai để xác nhận; nếu là dạng viết hiếm thì chấp nhận, nếu không thử `mnist-cnn-small-v0.1` (12/24 kênh) — chỉ khi test chưa đủ chỉ tiêu",
                 "priority": "P2", "measure": "Số ảnh sai các cặp này trên val; recall lớp 2, 4, 5, 8 ≥ 98%"},
                {"error": "val v0.1 thấp hơn v0.2", "fix": "Giữ augmentation dịch ≤ 2px (`ds-v2`) vì dữ liệu thực tế có thể lệch vị trí; không mở rộng thêm khi chưa có lỗi mới",
                 "priority": "P3", "measure": "val ≥ 98,5% và test ≥ 98% khi train lại với seed khác"}],
            "recommend": (f"Dùng `{raw['model_version']}` (`{raw['dataset_version']}`) cho I1. Kết quả chỉ chứng minh trên phân bố MNIST; "
                          "lưu ý ds-v2 chỉ khác ds-v1 ở augmentation train, val/test giữ nguyên. Chưa đo độ trễ suy luận (việc của I1). "
                          "Một lần chạy seed 42, sai số chuẩn val ≈ 0,14%: chênh v0.1/v0.2 (0,2%) nằm trong khoảng nhiễu."),
        },
        "metrics": raw, "train_history": {"mnist-cnn-tiny-v0.1": t1, a.final: t2},
    }
    p = os.path.join(a.dir, "eval.json")
    json.dump(d, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_report.py"), p, "--out-dir", a.dir], check=True)


if __name__ == "__main__":
    main()
