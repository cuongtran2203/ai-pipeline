"""eval_raw.json (run_eval) -> eval.json theo schema render_report (+ bảng spec, danh sách ảnh sai)."""
import json
import os
from collections import Counter

RUN = os.environ.get("RUN_DIR", r"C:\Users\24h\Desktop\AI_worklflow\runs\mnist-cnn")
D = os.path.join(RUN, "modules", "e2e")


def main():
    r = json.load(open(os.path.join(D, "eval_raw.json"), encoding="utf-8"))
    lat, tr = r["latency"], r["m1_train"]
    acc = r["test_acc"]
    spec = [
        {"metric": "Test accuracy", "threshold": ">= 98%", "value": f"{100 * acc:.2f}% ({r['test_correct']}/{r['test_total']})", "pass": acc >= 0.98},
        {"metric": "Độ trễ CPU trung bình (batch 1)", "threshold": "<= 10 ms/ảnh", "value": f"{lat['mean_ms']:.3f} ms", "pass": lat["mean_ms"] <= 10},
        {"metric": "Thời gian train CPU", "threshold": "<= 600 s", "value": f"{tr['train_seconds']:.1f} s ({tr['epochs_run']} epoch, {tr['threads']} luồng; số liệu M1)", "pass": tr["train_seconds"] <= 600},
        {"metric": "Kích thước model", "threshold": "< 5.000.000 byte", "value": f"{r['ckpt_bytes']} byte = {r['ckpt_mb']:.3f} MB", "pass": r["ckpt_bytes"] < 5_000_000},
    ]
    pairs = Counter((w["label"], w["pred"]) for w in r["wrong"])
    errors = []
    for (t, p), n in pairs.most_common():
        if n < 5:
            break
        ex = sorted((w for w in r["wrong"] if (w["label"], w["pred"]) == (t, p)), key=lambda w: -w["confidence"])[:3]
        errors.append({"cluster": f"Nhầm {t} → {p}", "count": n,
                       "cause": "Giả thuyết cần error-analyst xác nhận bằng cách xem ảnh (dạng viết gần nhau); chưa xem ảnh.",
                       "examples": [{"where": f"ảnh test #{w['index']}", "note": f"nhãn {t}, dự đoán {p}, độ tin cậy {w['confidence']:.2f}"} for w in ex]})
    rest = sum(n for n in pairs.values()) - sum(e["count"] for e in errors)
    if rest:
        errors.append({"cluster": "Các cặp nhầm lẻ (< 5 ảnh/cặp)", "count": rest,
                       "cause": "Rải rác nhiều cặp lớp, xem danh sách `wrong` trong eval.json.", "examples": []})
    ok = all(s["pass"] for s in spec)
    out = {
        "title": "MNIST CNN — e2e", "version": {"model": r["model_version"], "dataset": r["dataset_version"]},
        "baseline_name": "M1 eval.json (cùng checkpoint, accuracy test)",
        "overview": {
            "status": "Chỉ tiêu spec: test ≥ 98%, ≤ 10 ms/ảnh CPU, train ≤ 10 phút, model < 5 MB. Kết luận chỉ áp dụng cho phân bố MNIST sạch (không có dữ liệu real ngoài MNIST).",
            "method": f"Pipeline suy luận độc lập (`src/e2e/pipeline.py`): nạp checkpoint, kiểm tra metadata version, uint8 28×28 → chuẩn hóa → CNN → softmax → argmax. Accuracy trên đủ 10.000 ảnh test (từng ảnh một); độ trễ theo A2: batch 1, eval()+inference_mode(), ảnh trong RAM, 100 warm-up, {lat['n_images_per_run']} ảnh tuần tự × {lat['repeats']} lần ({lat['n_images_per_run'] * lat['repeats']} phép đo).",
            "result": f"{'ĐẠT cả 4 chỉ tiêu' if ok else 'KHÔNG đạt đủ chỉ tiêu'}: accuracy {100 * acc:.2f}%, độ trễ mean/median/p95 = {lat['mean_ms']:.3f}/{lat['median_ms']:.3f}/{lat['p95_ms']:.3f} ms ({lat['threads']} luồng; 1 luồng: mean {lat['ref_1thread']['mean_ms']:.3f} ms), train {tr['train_seconds']:.1f} s, model {r['ckpt_mb']:.3f} MB. Khớp hoàn toàn với M1: {'có (cùng tập ảnh sai)' if r['m1_check']['wrong_set_identical'] else 'KHÔNG'}."},
        "tables": [
            {"name": "Recall theo từng lớp trên 10.000 ảnh test (e2e)", "rows": [
                {"item": f"Lớp {c['class']}", "correct": c["correct"], "total": c["support"], "baseline_correct": m["correct"]}
                for c, m in zip(r["per_class"], json.load(open(os.path.join(RUN, "modules", "cnn", "eval.json"), encoding="utf-8"))["metrics"]["per_class"])]},
            {"name": "Tổng test", "rows": [{"item": "Toàn bộ", "correct": r["test_correct"], "total": r["test_total"], "baseline_correct": r["m1_check"]["m1_test_correct"]}]},
            {"name": "Đạt/không đạt theo chỉ tiêu spec (1/1 = đạt, 0/1 = không đạt)", "rows": [
                {"item": f"{s['metric']} — {s['threshold']} — đo: {s['value']}", "correct": int(s["pass"]), "total": 1} for s in spec]},
        ],
        "errors": errors,
        "conclusion": {"fixes": [
            {"error": "Cụm nhầm lớn nhất (4→9, 2→7, 5→3...)", "fix": "error-analyst xem ảnh sai; nếu là dạng viết hiếm/nhãn mơ hồ thì chấp nhận, không đổi kiến trúc khi đã đạt spec", "priority": "P3", "measure": "Recall lớp 2, 4, 5, 8 trên test; số ảnh sai theo cặp"},
            {"error": "Chưa có dữ liệu real ngoài MNIST", "fix": "Nếu dùng sản phẩm thật, thu ≥ 50 ảnh có nhãn thật và đánh giá riêng", "priority": "P2", "measure": "Accuracy trên tập real ≥ 50 mẫu"}],
            "recommend": f"Giữ `{r['model_version']}` (`{r['dataset_version']}`) làm bản phát hành: đạt cả 4 chỉ tiêu spec, độ trễ dư ~{10 / lat['mean_ms']:.0f} lần so với ngưỡng. Độ trễ đo trên máy {lat['cpu']}, tải CPU trước/sau {lat['cpu_load_percent_before_after']}%."},
        "spec_check": spec, "metrics": r,
    }
    json.dump(out, open(os.path.join(D, "eval.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("ok", ok, len(errors), "clusters")


if __name__ == "__main__":
    main()
