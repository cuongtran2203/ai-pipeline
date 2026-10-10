"""Pipeline suy luận độc lập: ảnh uint8 28x28 -> nhãn + softmax. Checkpoint .pt có metadata version."""
import numpy as np
import torch

from src.cnn.model import MnistCNN  # chỉ lấy định nghĩa kiến trúc (src/cnn thuộc M1, không sửa)

REQUIRED_KEYS = ("state_dict", "model_version", "dataset_version", "arch", "norm")


class Pipeline:
    def __init__(self, ckpt_path, expect_model=None, expect_dataset=None):
        ck = torch.load(ckpt_path, map_location="cpu")
        missing = [k for k in REQUIRED_KEYS if k not in ck]
        if missing:
            raise ValueError(f"checkpoint thiếu metadata: {missing}")
        if expect_model and ck["model_version"] != expect_model:
            raise ValueError(f"model_version {ck['model_version']} != {expect_model}")
        if expect_dataset and ck["dataset_version"] != expect_dataset:
            raise ValueError(f"dataset_version {ck['dataset_version']} != {expect_dataset}")
        a = ck["arch"]
        self.net = MnistCNN(a["c1"], a["c2"], a["fc"])
        self.net.load_state_dict(ck["state_dict"])
        self.net.eval()
        self.model_version, self.dataset_version = ck["model_version"], ck["dataset_version"]
        self.mean, self.std = float(ck["norm"]["mean"]), float(ck["norm"]["std"])

    def predict_proba(self, img_u8):
        """img_u8: uint8 [28,28] hoặc [N,28,28] -> probs float32 [N,10]."""
        a = np.asarray(img_u8)
        if a.dtype != np.uint8 or a.shape[-2:] != (28, 28):
            raise ValueError("đầu vào phải là uint8 28x28")
        x = torch.from_numpy(np.ascontiguousarray(a.reshape(-1, 1, 28, 28))).float()
        x = x.div_(255.0).sub_(self.mean).div_(self.std)
        with torch.inference_mode():
            return torch.softmax(self.net(x), dim=1)

    def predict(self, img_u8):
        """Một ảnh -> (label:int, probabilities:list[10])."""
        p = self.predict_proba(img_u8)
        return int(p.argmax(1)[0]), p[0].tolist()
