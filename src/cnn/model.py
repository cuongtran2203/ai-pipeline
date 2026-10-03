from torch import nn

VERSIONS = {"mnist-cnn-tiny": (8, 16), "mnist-cnn-small": (12, 24)}


class MnistCNN(nn.Module):
    def __init__(self, c1=8, c2=16, fc=32):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, c1, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(c1, c2, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2))
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(c2 * 49, fc), nn.ReLU(), nn.Linear(fc, 10))

    def forward(self, x):
        return self.head(self.features(x))


def build(name):
    # name dạng mnist-cnn-tiny-v0.1 / mnist-cnn-tiny-v0.2 / mnist-cnn-small-v0.1
    c1, c2 = VERSIONS[name.rsplit("-v", 1)[0]]
    return MnistCNN(c1, c2)


def n_params(m):
    return sum(p.numel() for p in m.parameters())
