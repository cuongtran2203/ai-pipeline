"""Train. Dùng: python -m src.cnn.train --model mnist-cnn-tiny-v0.1 --dataset ds-v1 [--aug-shift 2] --out DIR"""
import argparse
import json
import os
import time

import numpy as np
import torch
from torch import nn

from . import data, model as M


@torch.inference_mode()
def predict(net, x, bs=1000):
    net.eval()
    return torch.cat([net(x[i:i + bs]) for i in range(0, len(x), bs)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mnist-cnn-tiny-v0.1")
    ap.add_argument("--dataset", default="ds-v1")
    ap.add_argument("--aug-shift", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)
    torch.manual_seed(42)
    rng = np.random.default_rng(42)
    xtr, ytr, xva, yva, _, _ = data.load_uint8(with_test=False)  # train không chạm test
    xva_t, yva_t = data.normalize(xva), torch.from_numpy(yva).long()
    ytr_t = torch.from_numpy(ytr).long()
    xtr_fix = data.normalize(xtr)
    net = M.build(a.model)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss()
    os.makedirs(a.out, exist_ok=True)
    ckpt = os.path.join(a.out, a.model + ".pt")
    hist, best = [], -1.0
    t0 = time.perf_counter()
    for ep in range(1, a.epochs + 1):
        net.train()
        x = data.normalize(data.random_shift(xtr, a.aug_shift, rng)) if a.aug_shift else xtr_fix
        perm = torch.randperm(len(x))
        tl = 0.0
        for i in range(0, len(x), 128):
            idx = perm[i:i + 128]
            opt.zero_grad()
            loss = lossf(net(x[idx]), ytr_t[idx])
            loss.backward()
            opt.step()
            tl += loss.item() * len(idx)
        va = (predict(net, xva_t).argmax(1) == yva_t).float().mean().item()
        hist.append({"epoch": ep, "train_loss": tl / len(x), "val_acc": va})
        print(hist[-1], flush=True)
        if va > best:
            best = va
            torch.save({"state_dict": net.state_dict(), "model_version": a.model,
                        "dataset_version": a.dataset, "epoch": ep, "val_acc": va,
                        "arch": {"c1": net.features[0].out_channels, "c2": net.features[3].out_channels, "fc": 32},
                        "norm": {"mean": data.MEAN, "std": data.STD}}, ckpt)
    secs = time.perf_counter() - t0
    info = {"model_version": a.model, "dataset_version": a.dataset, "train_seconds": secs,
            "epochs_run": a.epochs, "best_val_acc": best,
            "best_epoch": max(hist, key=lambda h: h["val_acc"])["epoch"],
            "history": hist, "params": M.n_params(net), "ckpt_bytes": os.path.getsize(ckpt),
            "threads": torch.get_num_threads(), "aug_shift": a.aug_shift, "torch": torch.__version__}
    json.dump(info, open(os.path.join(a.out, f"train_{a.model}_{a.dataset}.json"), "w"), indent=1)
    print("train_seconds", round(secs, 1), "best_val", best, "bytes", info["ckpt_bytes"])


if __name__ == "__main__":
    main()
