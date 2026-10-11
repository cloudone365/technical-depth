import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def execute(self, requests):
        responses = []
        for req in requests:
            texts = pb_utils.get_input_tensor_by_name(req, "TEXT").as_numpy()
            ids = np.zeros((texts.shape[0], 16), dtype=np.int32)
            for i, t in enumerate(texts[:, 0]):
                words = t.decode().lower().split()[:16]
                ids[i, : len(words)] = [hash(w) % 32000 for w in words]
            responses.append(pb_utils.InferenceResponse([pb_utils.Tensor("IDS", ids)]))
        return responses
