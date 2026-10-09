"""Reuse the MoE4 GEMM data, but route deterministically to expert 0."""

import importlib.util
from pathlib import Path

import numpy as np

_source = Path(__file__).resolve().parent.parent / "moe4_4cluster/moe_datagen.py"
_spec = importlib.util.spec_from_file_location("moe4_datagen", _source)
_moe4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_moe4)
emit_header_file = _moe4.emit_header_file


def generate_moe_data(params):
    if params["num_experts"] != 2 or params["top_k"] != 1:
        raise ValueError("moe2_2cluster requires two experts and top-1 routing")
    data = _moe4.generate_moe_data(params)
    logits = np.array([2.0, -2.0], dtype=np.float32)
    exp_logits = np.exp(logits - np.max(logits))
    data["router_logits"] = logits
    data["softmax_golden"] = (exp_logits / np.sum(exp_logits)).astype(np.float32)
    return data
