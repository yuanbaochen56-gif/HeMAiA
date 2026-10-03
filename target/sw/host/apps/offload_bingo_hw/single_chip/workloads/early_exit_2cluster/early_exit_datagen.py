"""Deterministic single-row GEMM chain, using the existing block golden model."""
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[8]
sys.path.append(str(ROOT / "util/sim"))
import _usg_paths  # noqa: F401,E402
from sim_golden_models import block_gemm_golden_model


def requant(values, shift):
    if not 0 <= shift < 31:
        raise ValueError("shift must be in [0, 30]")
    return np.clip(np.asarray(values, dtype=np.int32) >> shift, -128, 127).astype(np.int8)


def generate_data(params):
    m, k, n = (params[name] for name in ("M", "K", "N"))
    row, tile, col = (params[name] for name in ("meshRow", "tileSize", "meshCol"))
    # The MoE2 shape is [1,16,32]. One row and one output tile make both
    # output and next-layer A layouts row-major, without a shared layout change.
    if (m, n, row) != (1, 1, 1) or n * col != k * tile:
        raise ValueError("early exit requires one row/tile and GEMM1 N == GEMM2 K")
    rng = np.random.default_rng(43)
    x = rng.integers(-4, 5, size=m*k*row*tile, dtype=np.int8)
    w1 = rng.integers(-4, 5, size=k*n*tile*col, dtype=np.int8)
    w2 = rng.integers(-4, 5, size=k*n*tile*col, dtype=np.int8)
    zero = np.zeros(m*n*row*col, dtype=np.int32)
    shallow = block_gemm_golden_model(m, k, n, row, tile, col, x, w1, 0, 0, zero)
    h8 = requant(shallow, params["shift"])
    deep = block_gemm_golden_model(m, k, n, row, tile, col, h8, w2, 0, 0, zero)
    # Independently verify block/row interpretation and int32 bounds.
    matrix_w = lambda w: w.reshape(k, col, tile).transpose(0, 2, 1).reshape(k*tile, col)
    np.testing.assert_array_equal(shallow, x.astype(np.int64) @ matrix_w(w1).astype(np.int64))
    np.testing.assert_array_equal(deep, h8.astype(np.int64) @ matrix_w(w2).astype(np.int64))
    assert not np.array_equal(shallow, deep)
    assert max(np.max(np.abs(shallow.astype(np.int64))),
               np.max(np.abs(deep.astype(np.int64)))) < 2**31
    a_alloc = ((len(x) + 64 + 63) // 64) * 64
    return dict(input_X=np.pad(x, (0, a_alloc-len(x))),
                weight_W1=w1, weight_W2=w2, shallow_golden=shallow,
                deep_golden=deep, h8_golden=h8), a_alloc


def emit_header(path, data):
    lines = ["#pragma once", "#include <stdint.h>"]
    for name, values in data.items():
        dtype = "int32_t" if name.endswith("golden") and name != "h8_golden" else "int8_t"
        lines.append(f'{dtype} {name}[{len(values)}] __attribute__((section(".wide_spm"))) = {{')
        for start in range(0, len(values), 16):
            lines.append("    " + ", ".join(str(int(v)) for v in values[start:start+16]) + ",")
        lines.append("};")
    path.write_text("\n".join(lines) + "\n")
