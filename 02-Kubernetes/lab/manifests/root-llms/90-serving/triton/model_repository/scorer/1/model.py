import numpy as np
import triton_python_backend_utils as pb_utils

try:
    import torch
except ImportError:  # CPU fallback keeps the lab working without torch in the image
    torch = None


class TritonPythonModel:
    def initialize(self, args):
        self.dev = "cuda" if torch is not None and torch.cuda.is_available() else "cpu"
        if torch is not None:
            g = torch.Generator().manual_seed(0)
            self.emb = torch.randn(32000, 256, generator=g).to(self.dev)
            self.w = torch.randn(256, 1, generator=g).to(self.dev)

    def execute(self, requests):
        out = []
        for req in requests:
            ids = pb_utils.get_input_tensor_by_name(req, "IDS").as_numpy()
            if torch is None:
                score = (ids.astype(np.float32).mean(axis=1, keepdims=True) / 32000.0)
            else:
                x = self.emb[torch.from_numpy(ids).long().to(self.dev)].mean(dim=1)
                score = torch.sigmoid(x @ self.w).float().cpu().numpy()
            out.append(pb_utils.InferenceResponse([pb_utils.Tensor("SCORE", score.astype(np.float32))]))
        return out
