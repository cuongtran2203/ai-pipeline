"""Dataset ds-v1 / ds-v2: MNIST idx (cache runs/mnist-cnn/data/MNIST/raw), val split seed 42."""
import gzip
import os

import numpy as np
import torch

MEAN, STD = 0.1307, 0.3081
RUN_DIR = os.environ.get("RUN_DIR", r"C:\Users\24h\Desktop\AI_worklflow\runs\mnist-cnn")
RAW = os.path.join(RUN_DIR, "data", "MNIST", "raw")
VAL_IDX = os.path.join(RUN_DIR, "artifacts", "A1", "val_indices_seed42.npy")


def _idx(name, labels):
    p = os.path.join(RAW, name)
    if os.path.exists(p):
        buf = open(p, "rb").read()
    else:
        buf = gzip.open(p + ".gz", "rb").read()
    a = np.frombuffer(buf, np.uint8, offset=8 if labels else 16)
    return a.copy() if labels else a.reshape(-1, 28, 28).copy()


def load_uint8(with_test=True):
    """Trả về (xtr, ytr, xva, yva, xte, yte) uint8; split theo ds-v1.
    with_test=False: không nạp test (xte, yte = None) để train không chạm test."""
    x, y = _idx("train-images-idx3-ubyte", False), _idx("train-labels-idx1-ubyte", True)
    xte = yte = None
    if with_test:
        xte, yte = _idx("t10k-images-idx3-ubyte", False), _idx("t10k-labels-idx1-ubyte", True)
    vi = np.load(VAL_IDX)
    assert len(vi) == 5000 and len(set(vi.tolist())) == 5000
    mask = np.ones(len(x), bool)
    mask[vi] = False
    return x[mask], y[mask], x[vi], y[vi], xte, yte


def normalize(x_u8):
    t = torch.from_numpy(x_u8).float().div_(255.0).sub_(MEAN).div_(STD)
    return t.unsqueeze(1) if t.ndim == 3 else t


def random_shift(x_u8, max_px, rng):
    """Augmentation train-only (ds-v2): dịch ngẫu nhiên <= max_px, nền 0."""
    n = len(x_u8)
    out = np.zeros_like(x_u8)
    dx = rng.integers(-max_px, max_px + 1, n)
    dy = rng.integers(-max_px, max_px + 1, n)
    for i in range(n):
        a, b = dy[i], dx[i]
        ys, yd = (slice(0, 28 - a), slice(a, 28)) if a >= 0 else (slice(-a, 28), slice(0, 28 + a))
        xs, xd = (slice(0, 28 - b), slice(b, 28)) if b >= 0 else (slice(-b, 28), slice(0, 28 + b))
        out[i, yd, xd] = x_u8[i, ys, xs]
    return out
