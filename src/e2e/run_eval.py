"""E2E: accuracy 10.000 test + latency CPU batch 1 (quy trình A2) -> modules/e2e/eval_raw.json.
Dùng: python -m src.e2e.run_eval [--ckpt ...] [--n-lat 2000]"""
import argparse
import gzip
import json
import os
import platform
import subprocess
import time

import numpy as np
import torch

from .pipeline import Pipeline

RUN = os.environ.get("RUN_DIR", r"C:\Users\24h\Desktop\AI_worklflow\runs\mnist-cnn")
RAW = os.path.join(RUN, "data", "MNIST", "raw")
CNN = os.path.join(RUN, "modules", "cnn")


def idx(name, labels):
    p = os.path.join(RAW, name)
    buf = open(p, "rb").read() if os.path.exists(p) else gzip.open(p + ".gz", "rb").read()
    a = np.frombuffer(buf, np.uint8, offset=8 if labels else 16)
    return a.copy() if labels else a.reshape(-1, 28, 28).copy()


def ps(cmd):
    try:
        return subprocess.check_output(["powershell", "-NoProfile", "-Command", cmd], text=True).strip()
    except Exception:
        return None


def prep(im, mean, std):
    return torch.from_numpy(im).reshape(1, 1, 28, 28).float().div_(255.0).sub_(mean).div_(std)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=os.path.join(CNN, "mnist-cnn-tiny-v0.2.pt"))
    ap.add_argument("--m1-eval", default=os.path.join(CNN, "eval.json"))
    ap.add_argument("--out", default=os.path.join(RUN, "modules", "e2e", "eval_raw.json"))
    ap.add_argument("--n-lat", type=int, default=2000)
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()

    m1 = json.load(open(a.m1_eval, encoding="utf-8"))["metrics"]
    pl = Pipeline(a.ckpt, expect_model=m1["model_version"], expect_dataset=m1["dataset_version"])
    xte, yte = idx("t10k-images-idx3-ubyte", False), idx("t10k-labels-idx1-ubyte", True)
    assert len(xte) == 10000 and xte.dtype == np.uint8

    # Accuracy: pipeline từng ảnh một (đúng hợp đồng I/O) trên 10.000 ảnh test
    preds, probs = np.zeros(10000, int), np.zeros((10000, 10), np.float32)
    for i in range(10000):
        l, p = pl.predict(xte[i])
        preds[i], probs[i] = l, p
    assert np.allclose(probs.sum(1), 1, atol=1e-4) and probs.min() >= 0 and probs.max() <= 1
    correct = int((preds == yte).sum())
    cm = np.zeros((10, 10), int)
    for t, p in zip(yte, preds):
        cm[t, p] += 1
    wrong = np.where(preds != yte)[0]
    m1_wrong = set(m1["wrong_indices"])

    # Latency (A2): batch 1, ảnh trong RAM, 100 warm-up, n_lat ảnh tuần tự, lặp nhiều lần
    imgs = [np.ascontiguousarray(xte[i]) for i in range(a.n_lat)]
    net, mean, std = pl.net, pl.mean, pl.std
    load_cmd = "(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average"
    load_before = ps(load_cmd)
    runs = []
    with torch.inference_mode():
        for k in range(100):
            torch.softmax(net(prep(imgs[k], mean, std)), dim=1).argmax(1).item()
        for _ in range(a.repeats):
            ts = np.empty(len(imgs))
            for k, im in enumerate(imgs):
                t0 = time.perf_counter_ns()
                torch.softmax(net(prep(im, mean, std)), dim=1).argmax(1).item()
                ts[k] = (time.perf_counter_ns() - t0) / 1e6
            runs.append(ts)
    load_after = ps(load_cmd)
    allt = np.concatenate(runs)
    lat = {"n_images_per_run": len(imgs), "repeats": a.repeats, "warmup": 100,
           "mean_ms": float(allt.mean()), "median_ms": float(np.median(allt)),
           "p95_ms": float(np.percentile(allt, 95)), "max_ms": float(allt.max()),
           "mean_ms_per_run": [float(r.mean()) for r in runs],
           "threads": torch.get_num_threads(),
           "cpu": ps("(Get-CimInstance Win32_Processor).Name"), "logical_cpus": os.cpu_count(),
           "cpu_load_percent_before_after": [load_before, load_after],
           "torch": torch.__version__, "python": platform.python_version(), "os": platform.platform()}
    torch.set_num_threads(1)  # tham khảo: 1 luồng
    ts = np.empty(len(imgs))
    with torch.inference_mode():
        for k, im in enumerate(imgs):
            t0 = time.perf_counter_ns()
            torch.softmax(net(prep(im, mean, std)), dim=1).argmax(1).item()
            ts[k] = (time.perf_counter_ns() - t0) / 1e6
    lat["ref_1thread"] = {"mean_ms": float(ts.mean()), "median_ms": float(np.median(ts)),
                          "p95_ms": float(np.percentile(ts, 95))}

    out = {"model_version": pl.model_version, "dataset_version": pl.dataset_version,
           "eval_dataset": "ds-v1 test gốc (10.000 ảnh; ds-v2 chỉ khác ở augmentation train)",
           "test_correct": correct, "test_total": 10000, "test_acc": correct / 10000,
           "per_class": [{"class": c, "support": int(cm[c].sum()), "correct": int(cm[c, c]),
                          "recall": float(cm[c, c] / cm[c].sum())} for c in range(10)],
           "confusion_matrix": cm.tolist(),
           "m1_check": {"m1_test_correct": m1["test_correct"], "match_acc": correct == m1["test_correct"],
                        "wrong_set_identical": set(wrong.tolist()) == m1_wrong,
                        "only_e2e": sorted(set(wrong.tolist()) - m1_wrong),
                        "only_m1": sorted(m1_wrong - set(wrong.tolist()))},
           "latency": lat, "ckpt_path": a.ckpt, "ckpt_bytes": os.path.getsize(a.ckpt),
           "ckpt_mb": os.path.getsize(a.ckpt) / 1e6,
           "m1_train": {"train_seconds": m1["train_seconds"], "epochs_run": m1["epochs_run"],
                        "threads": m1["threads"]},
           "wrong": [{"index": int(i), "label": int(yte[i]), "pred": int(preds[i]),
                      "confidence": float(probs[i, preds[i]]), "p_label": float(probs[i, yte[i]])}
                     for i in wrong]}
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print({k: out[k] for k in ("test_correct", "test_acc", "m1_check")})
    print({k: lat[k] for k in ("mean_ms", "median_ms", "p95_ms", "threads", "cpu_load_percent_before_after", "ref_1thread")})


if __name__ == "__main__":
    main()
