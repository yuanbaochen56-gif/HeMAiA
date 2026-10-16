"""Compile-time task-time estimates for the bingo HW manager watchdog.

The watchdog (bingo_hw_manager_watchdog) times every busy device slot in watchdog
ticks: manager clock cycles, counted at the normal power level
(bingo_hw_manager_wd_tick; BINGO_WD_TICK_PS in occamy.h). A task gets two numbers:
  E_tot  ticks from its dispatch to its done;
  E_int  the longest interval without progress (dispatch, heartbeat or done),
         E_int = E_tot >> S. S comes from the kernel's heartbeat structure; 0 when
         unknown (E_int = E_tot, loose but safe).

Where an estimate comes from (estimate()):
  - warm execution: the slowest warm execution of the same kernel and size key in
    the calibration runs of the platform (a measured worst healthy context);
    otherwise the kernel family's T = a + b * work, an upper envelope of the
    calibrated warm worst cases, fitted to at least two sizes and used up to
    EXTRAPOLATE times beyond the largest; otherwise none;
  - cold execution, the first one of a kernel on its core in an offload (the core
    fetches the kernel's code): the slowest cold execution of the same kernel and
    size key, otherwise warm + the kernel's cold term (the largest cold - warm
    difference of a calibrated size), otherwise none;
  - kernels in NO_ESTIMATE (their time depends on data or on another task) and
    host tasks (the host slot is not watched) get none.
A missing estimate is always safe: the watchdog then keeps its other thresholds.
Every number is in ticks, rounded up.

Calibration: the healthy runs of the thesis evaluation on bingo 6eea1fd / HeMAiA
aec4a53f..e54e051e (full scene set, the learned-threshold sweeps, the scheduling and
four-cluster runs): 65 runs, 894 device executions. Durations are watchdog ticks
(cycles at a level slower than normal count normal/level). Runs with an injected
fault, an image without heartbeats or the SDC device runtime are not used.
Leave-one-workload-out on hemaia_ci (PM on): 479 estimates, 4.6 % below the actual
time, the worst 1.24x below (a cold 1 KiB copy); hemaia_ci_4cluster 1.31x. A
calibration does not carry over to another platform (2 -> 4 clusters: up to 3.4x
below) or device runtime (SDC: 3.3x), so each has its own table and the generated
code refuses other ones. PM off (cores stay at the boot divider, ticks not scaled):
1.3 .. 1.85x slower; the generated code doubles the estimates (no estimate below).
"""
from __future__ import annotations

from bisect import bisect_left
import math

EXIT_KERNELS = ("__snax_bingo_kernel_exit", "__host_bingo_kernel_exit")
NO_ESTIMATE = ("__snax_bingo_kernel_sdc_compare",)
EXTRAPOLATE = 2.0
PM_OFF_FACTOR = 2
# Interval classes per core type in the hardware (WdClassWidth = 3, class 0 unused)
CLASSES = 7
# Watchdog tick of the calibration and the normal level it was counted at
CAL_TICK_PS = 28000
CAL_NORMAL_LEVEL = 6

# platform -> geometry (clusters per chiplet, Snitch cores per cluster) and the
# calibration: warm / cold worst cases per (kernel, size key), cold terms per kernel,
# family models (a, b, largest calibrated work)
CALIBRATION = {
    "hemaia_ci": dict(
        geometry=(2, 2),
        warm={
            ('__snax_bingo_kernel_gemm_full', 'M=1 K=16 N=1'): 1193,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=1024 L3>L1'): 124,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=128 L1>L3'): 119,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=256 L3>L1'): 100,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=4096 L1>L3'): 157,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=64 L3>L1'): 92,
        },
        cold={
            ('__snax_bingo_kernel_exit', ''): 8473,
            ('__snax_bingo_kernel_gemm_full', 'M=1 K=16 N=1'): 2523,
            ('__snax_bingo_kernel_gemm_full', 'M=1 K=2 N=1 array_shape_idx=1'): 2596,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=1024 L3>L1'): 479,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=128 L3>L1'): 482,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=128 L3>L3'): 482,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=256 L3>L1'): 510,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=512 L3>L1'): 455,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=64 L3>L1'): 332,
            ('__snax_bingo_kernel_int32_add', 'n=32'): 4222,
            ('__snax_bingo_kernel_int32_add', 'n=512'): 64925,
        },
        cold_term={
            '__snax_bingo_kernel_gemm_full': 1330,
            '__snax_bingo_kernel_idma_1d_copy': 410,
        },
        models={
            'copy L1>L3': (116.98, 0.0096, 4096),
            'copy L3>L1': (90.92, 0.0324, 1024),
        }),
    "hemaia_ci_4cluster": dict(
        geometry=(4, 2),
        warm={
            ('__snax_bingo_kernel_gemm_full', 'M=2 K=4 N=1'): 1508,
            ('__snax_bingo_kernel_gemm_full', 'M=2 K=4 N=2'): 2163,
            ('__snax_bingo_kernel_gemm_full', 'M=2 K=8 N=1'): 1424,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=1024 L3>L1'): 211,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=128 L1>L3'): 196,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=16384 L1>L3'): 878,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=256 L3>L1'): 105,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=512 L3>L1'): 153,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=8192 L1>L3'): 672,
            ('__snax_bingo_kernel_xdma_d_to_row_major', 'M_T=2 N_T=1'): 4201,
            ('__snax_bingo_kernel_xdma_d_to_row_major', 'M_T=2 N_T=2'): 4663,
        },
        cold={
            ('__snax_bingo_kernel_exit', ''): 8489,
            ('__snax_bingo_kernel_gemm_full', 'M=1 K=2 N=1 array_shape_idx=1'): 2547,
            ('__snax_bingo_kernel_gemm_full', 'M=2 K=4 N=1'): 2584,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=128 L3>L1'): 566,
            ('__snax_bingo_kernel_idma_1d_copy', 'size=512 L3>L1'): 470,
            ('__snax_bingo_kernel_xdma_d_to_row_major', 'M_T=2 N_T=1'): 6873,
        },
        cold_term={
            '__snax_bingo_kernel_gemm_full': 1077,
            '__snax_bingo_kernel_idma_1d_copy': 317,
            '__snax_bingo_kernel_xdma_d_to_row_major': 2672,
        },
        models={
            'copy L1>L3': (274.06, 0.0485, 16384),
            'copy L3>L1': (80.01, 0.1425, 1024),
            'gemm': (852.21, 81.875, 16),
            'layout': (3738.96, 231.0, 4),
        }),
}
# Host kernels (the host backup of a device kernel, bingo_fence_fb), microseconds
HOST_US = {
    ('__host_bingo_kernel_idma', 128): 16.8,
}


def mem_level(handle, cluster: int) -> str:
    """Memory level of a copy operand: L1 of the executing cluster, L1r of another
    cluster, L2, L3. A C symbol, a fixed address or a plain integer is L3."""
    base = getattr(handle, "base", handle)            # BingoMemAllocView
    level = getattr(base, "mem_level", None)
    if level == "L1":
        return "L1" if getattr(base, "cluster_id", None) == cluster else "L1r"
    if level in ("L2", "L3"):
        return level
    return "L3"


GEMM_KEYS = ("M", "K", "N", "array_shape_idx", "quantization_enable", "accumPrevC", "int32tofp16_enable",
             "int4_a_enable", "int4_b_enable", "transpose_A", "transpose_B")


def task_key(kernel: str, ints: dict, src_level: str | None = None, dst_level: str | None = None):
    """(family, size key, work) of a device kernel from its integer arguments: the
    arguments its time depends on. The same function keys the calibration runs."""
    k = kernel.removeprefix("__snax_bingo_kernel_")
    if k == "idma_1d_copy":
        return "copy", f"size={ints.get('size')} {src_level}>{dst_level}", ints.get("size")
    if k == "gemm_full":
        work = None
        if all(isinstance(ints.get(n), int) for n in "MKN"):
            work = ints["M"] * ints["K"] * ints["N"]
        return "gemm", " ".join(f"{n}={ints.get(n)}" for n in GEMM_KEYS
                                if ints.get(n) not in (None, 0) or n in ("M", "K", "N")), work
    if k == "int32_add":
        return "eltwise", f"n={ints.get('num_elements')}", ints.get("num_elements")
    if k == "sdc_compare":
        return "compare", f"size={ints.get('size')}", ints.get("size")
    if k == "xdma_d_to_row_major":
        m, n = ints.get("M_T"), ints.get("N_T")
        return "layout", f"M_T={m} N_T={n}", m * n if isinstance(m, int) and isinstance(n, int) else None
    if k == "exit":
        return "exit", "", 0
    return k, " ".join(f"{a}={ints[a]}" for a in sorted(ints)), None


def model_key(kernel: str, family: str, key: str) -> str:
    """Model group: the family, for copies with the direction."""
    if family == "copy":
        return f"copy {key.split(' ')[1]}"
    return family


def interval_shift(kernel: str, ints: dict) -> int:
    """S of E_int = E_tot >> S from the kernel's heartbeat structure: the device
    int32 add beats every 256 elements (snax_bingo_kernel_int32_add), so n elements
    are n / 256 segments; every other kernel 0 (unknown or one segment)."""
    if kernel == "__snax_bingo_kernel_int32_add" and isinstance(ints.get("num_elements"), int):
        segments = max(1, ints["num_elements"] // 256)
        return min(3, segments.bit_length() - 1)
    return 0


def platform_name(num_clusters: int, num_cores: int, tick_ps: int | None) -> str | None:
    """The calibration of a platform (clusters per chiplet, Snitch cores per cluster,
    watchdog tick), or None."""
    if tick_ps != CAL_TICK_PS:
        return None
    for name, cal in CALIBRATION.items():
        if cal["geometry"] == (num_clusters, num_cores):
            return name
    return None


def args_ints(args) -> dict:
    """The integer arguments of a kernel argument object."""
    return {k: v for k, v in vars(args).items() if isinstance(v, int) and not isinstance(v, bool)} \
        if args is not None else {}


def node_key(node):
    """(family, size key, work, ints) of a node, from the compiler's objects."""
    ints = args_ints(node.kernel_args)
    src = dst = None
    if node.kernel_name == "__snax_bingo_kernel_idma_1d_copy":
        src = mem_level(getattr(node.kernel_args, "src_addr", None), node.assigned_cluster_id)
        dst = mem_level(getattr(node.kernel_args, "dst_addr", None), node.assigned_cluster_id)
    family, key, work = task_key(node.kernel_name, ints, src, dst)
    return family, key, work, ints


def warm_estimate(cal: dict, kernel: str, family: str, key: str, work):
    if (kernel, key) in cal["warm"]:
        return cal["warm"][(kernel, key)], "measured"
    model = cal["models"].get(model_key(kernel, family, key))
    if model is None or work is None or work > EXTRAPOLATE * model[2]:
        return None, "none"
    return model[0] + model[1] * work, "model"


def estimate(kernel: str, family: str, key: str, work, cold: bool, platform: str | None):
    """(E_tot ticks rounded up, source) or (None, reason): the rule of the module docstring."""
    if platform is None:
        return None, "no calibration of this platform"
    if not kernel or not kernel.startswith("__snax_"):
        return None, "not a device kernel"
    if kernel in NO_ESTIMATE:
        return None, "time depends on another task"
    cal = CALIBRATION[platform]
    if cold:
        if (kernel, key) in cal["cold"]:
            return math.ceil(cal["cold"][(kernel, key)]), "measured (cold)"
        value, source = warm_estimate(cal, kernel, family, key, work)
        if value is None:
            return None, "no measurement and no model"
        if kernel not in cal["cold_term"]:
            return None, "no cold term"
        return math.ceil(value + cal["cold_term"][kernel]), f"{source} + cold term"
    value, source = warm_estimate(cal, kernel, family, key, work)
    if value is None and (kernel, key) in cal["cold"]:
        return math.ceil(cal["cold"][(kernel, key)]), "measured (cold)"
    if value is None:
        return None, "no measurement and no model"
    return math.ceil(value), source


# ---------------------------------------------------------------- hardware format
def encode(ticks: int) -> int:
    """8-bit e4m4 code of an estimate, rounded up: code (e << 4) | m stands for
    (16 + m) << e ticks; 0 = no estimate. Values below 17 take code 1 (17 ticks);
    values above the largest code (1015808 ticks) have no code (0)."""
    if ticks <= 0:
        return 0
    for e in range(16):
        m = -(-ticks // (1 << e)) - 16
        if m <= 15:
            code = (e << 4) | max(m, 0)
            return code if code else 1
    return 0


def decode(code: int) -> int:
    """Ticks of an 8-bit code (0: no estimate)."""
    if code == 0:
        return 0
    return (16 + (code & 0xF)) << (code >> 4)


def entry(e_tot: int, s: int) -> int:
    """10-bit table entry: [7:0] e4m4 E_tot, [9:8] S."""
    code = encode(e_tot)
    return (s & 3) << 8 | code if code else 0


def bucket_shift(e_tot_code: int, e_ints: list[int]) -> int:
    """The largest S in 0..3 with decode(code) >> S still at least every task's E_int."""
    total = decode(e_tot_code)
    need = max(e_ints)
    s = 0
    while s < 3 and (total >> (s + 1)) >= need:
        s += 1
    return s


def buckets(values: list[int], count: int) -> list[list[int]]:
    """Split the sorted distinct VALUES into at most COUNT contiguous groups so that
    the largest ratio max / min within a group is the smallest (dynamic programming).
    Ties: the split with fewer groups, then the earlier cuts."""
    values = sorted(set(values))
    n = len(values)
    if n == 0:
        return []
    count = max(1, min(count, n))
    inf = float("inf")
    # best[k][i]: the smallest worst ratio of values[:i] in k groups
    best = [[inf] * (n + 1) for _ in range(count + 1)]
    cut = [[0] * (n + 1) for _ in range(count + 1)]
    best[0][0] = 1.0
    for k in range(1, count + 1):
        for i in range(1, n + 1):
            for j in range(k - 1, i):
                ratio = max(best[k - 1][j], values[i - 1] / values[j])
                if ratio < best[k][i] - 1e-12:
                    best[k][i], cut[k][i] = ratio, j
    k = min(range(1, count + 1), key=lambda k: (round(best[k][n], 9), k))
    groups, i = [], n
    while k:
        j = cut[k][i]
        groups.append(values[j:i])
        i, k = j, k - 1
    return groups[::-1]


def host_us(kernel: str, ints: dict):
    """A host kernel's slowest measured time (us) at this size, or None."""
    return HOST_US.get((kernel, ints.get("size", ints.get("num_elements"))))


def ticks_to_us(ticks: float, tick_ps: int = CAL_TICK_PS) -> float:
    return ticks * tick_ps / 1e6


def bucket_index(groups: list[list[int]], value: int) -> int:
    """1-based group of VALUE."""
    lows = [g[0] for g in groups]
    return bisect_left(lows, value + 1)
