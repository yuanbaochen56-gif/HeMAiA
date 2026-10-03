"""Search two separated integer margins with B3's weights and GEMM formula."""
from pathlib import Path
import runpy

import numpy as np

B3 = Path(__file__).resolve().parent.parent / "early_exit_2cluster"
shared = runpy.run_path(str(B3 / "early_exit_datagen.py"))
requant = shared["requant"]
emit_header = shared["emit_header"]


def margin(values):
    largest = sorted(int(value) for value in values)[-2:]
    return largest[1] - largest[0]


def generate_data(params):
    baseline, size = shared["generate_data"](params)
    m, k, n, row, tile, col = (params[name] for name in
                               ("M", "K", "N", "meshRow", "tileSize", "meshCol"))
    threshold = params["threshold"]
    if not isinstance(threshold, int) or not 0 < threshold <= 0xFFFFFFFF:
        raise ValueError("threshold must be a positive uint32")
    w1, w2 = baseline["weight_W1"], baseline["weight_W2"]
    zero = np.zeros(m * n * row * col, dtype=np.int32)
    model = shared["block_gemm_golden_model"]
    matrix = lambda w: w.reshape(k, col, tile).transpose(0, 2, 1).reshape(k * tile, col)
    rng = np.random.default_rng(45)
    samples, shallow_goldens, deep_goldens, margins = [], [], [], []
    for sample in range(2):
        bound = 4 if sample == 0 else 16
        for attempt in range(10000):
            x = rng.integers(-bound, bound + 1, size=m * k * row * tile, dtype=np.int8)
            shallow = model(m, k, n, row, tile, col, x, w1, 0, 0, zero)
            h8 = requant(shallow, params["shift"])
            deep = model(m, k, n, row, tile, col, h8, w2, 0, 0, zero)
            value = margin(shallow)
            separated = value * 2 <= threshold if sample == 0 else value >= 2 * threshold
            if not separated or np.array_equal(shallow, deep):
                continue
            for actual, lhs, weights in ((shallow, x, w1), (deep, h8, w2)):
                exact = lhs.astype(np.int64) @ matrix(weights).astype(np.int64)
                if np.any(exact < -(2**31)) or np.any(exact >= 2**31):
                    raise ValueError("GEMM golden overflows int32")
                np.testing.assert_array_equal(actual, exact)
            samples.append(np.pad(x, (0, size - len(x))))
            shallow_goldens.append(shallow)
            deep_goldens.append(deep)
            margins.append(value)
            break
        else:
            raise ValueError(f"fixed-seed search found no separated sample {sample}")
    return dict(X_samples=np.concatenate(samples), weight_W1=w1, weight_W2=w2,
                shallow_golden=np.concatenate(shallow_goldens),
                deep_golden=np.concatenate(deep_goldens)), size, margins
