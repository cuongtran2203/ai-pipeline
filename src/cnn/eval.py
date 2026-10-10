"""Đánh giá val/test từ checkpoint, ghi eval_raw.json. Test đánh giá 1 lần ở cuối."""
import argparse
import json
import os

import numpy as np
import torch

from . import data, model as M
from .train import predict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--train-json", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    ck = torch.load(a.ckpt, map_location="cpu")
    net = M.MnistCNN(ck["arch"]["c1"], ck["arch"]["c2"], ck["arch"]["fc"])
    net.load_state_dict(ck["state_dict"])
    tr = json.load(open(a.train_json))
    _, _, xva, yva, xte, yte = data.load_uint8()
    pv = predict(net, data.normalize(xva)).argmax(1).numpy()
    pt = predict(net, data.normalize(xte)).argmax(1).numpy()
    cm = np.zeros((10, 10), int)
    for t, p in zip(yte, pt):
        cm[t, p] += 1
    wrong = np.where(pt != yte)[0]
    out = {"model_version": ck["model_version"], "dataset_version": ck["dataset_version"],
           "val_acc": float((pv == yva).mean()), "test_correct": int((pt == yte).sum()),
           "test_total": len(yte), "test_acc": float((pt == yte).mean()),
           "per_class": [{"class": c, "support": int(cm[c].sum()), "correct": int(cm[c, c]),
                          "recall": float(cm[c, c] / cm[c].sum()),
                          "precision": float(cm[c, c] / max(cm[:, c].sum(), 1))} for c in range(10)],
           "confusion_matrix": cm.tolist(),
           "train_seconds": tr["train_seconds"], "epochs_run": tr["epochs_run"], "best_epoch": tr["best_epoch"],
           "params": tr["params"], "ckpt_bytes": os.path.getsize(a.ckpt),
           "ckpt_mb": os.path.getsize(a.ckpt) / 1e6, "threads": tr["threads"], "torch": tr["torch"],
           "wrong_indices": wrong.tolist(),
           "wrong_pairs": [[int(yte[i]), int(pt[i])] for i in wrong]}
    json.dump(out, open(a.out, "w"), indent=1)
    print({k: out[k] for k in ("val_acc", "test_acc", "train_seconds", "ckpt_bytes")})


if __name__ == "__main__":
    main()
