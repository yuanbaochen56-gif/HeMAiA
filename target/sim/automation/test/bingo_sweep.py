#!/usr/bin/env python3
r"""
Bingo recovery sweeps on HeMAiA
===============================
Runs ``3_start_bingo_watchdog_sim.py`` over parameter points and tabulates the
results with ``bingo_recovery_metrics.py``. Sets:

* ``idle_delay``  s6 (healthy), s7 (L1 recovery), s14 (s7 + boost 3) with the idle
                  entry delay (BINGO_PM_IDLE_ENTRY_DELAY, quad_ctrl cycles) in DELAYS
* ``timeouts``    s1, s6 (healthy: false positives?), s7 (L1), s10 (L3 loopback),
                  s12 (L2) with the watchdog heartbeat timeout in TIMEOUTS (confirm = 2x)

Each point is one driver run into ``<out-root>/<set>/<point>``; the tables go to
``<out-root>/<set>/summary.md`` (and .csv). The run stops before a point if the
home quota is within QUOTA_MARGIN_MB of its soft limit (the builds live in the repo).

    source ~micasusr/design/scripts/questasim_2025.2.rc; export MTI_VCO_MODE=64 QSIM_VCO_MODE=64
    python3 bingo_sweep.py --set idle_delay timeouts --out-root /volume1/users/r1015708/bingo_sweep
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "3_start_bingo_watchdog_sim.py"
METRICS = HERE / "bingo_recovery_metrics.py"
DELAYS = [0, 5000, 20000, 100000, 200000]
TIMEOUTS = [50000, 100000, 200000]
QUOTA_MARGIN_MB = 300

SETS = {
    "idle_delay": dict(scenarios=["s6", "s7", "s14"], baseline="s6",
                       points=[(f"d{d}", [f"--extra-flags=-DBINGO_PM_IDLE_ENTRY_DELAY={d}"]) for d in DELAYS]),
    "timeouts": dict(scenarios=["s1", "s6", "s7", "s10", "s12"], baseline="s6",
                     points=[(f"t{t}", ["--wd-timeout", str(t)]) for t in TIMEOUTS]),
}


def quota_ok() -> bool:
    out = subprocess.run(["quota", "-s"], capture_output=True, text=True).stdout
    m = re.search(r"\s(\d+)M\s+(\d+)M\s+(\d+)M", out)
    if not m:
        return True
    used, soft = int(m.group(1)), int(m.group(2))
    if used > soft - QUOTA_MARGIN_MB:
        print(f"[sweep] home quota {used}M of soft {soft}M: stopping", flush=True)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", nargs="+", choices=sorted(SETS), default=sorted(SETS))
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--bingo-repo", default=None)
    args = parser.parse_args()

    for name in args.set:
        cfg = SETS[name]
        set_root = args.out_root / name
        runs = []
        for point, opts in cfg["points"]:
            if not quota_ok():
                sys.exit(1)
            cmd = [sys.executable, str(DRIVER), "--scenario", *cfg["scenarios"],
                   "--out-root", str(set_root / point), *opts]
            if args.bingo_repo:
                cmd += ["--bingo-repo", args.bingo_repo]
            print(f"[sweep] {name} {point}: {' '.join(cmd)}", flush=True)
            rc = subprocess.run(cmd).returncode
            print(f"[sweep] {name} {point}: driver exit {rc}", flush=True)
            for sc in cfg["scenarios"]:
                if (set_root / point / sc).exists():
                    runs += ["--run", f"{sc}_{point}={set_root / point / sc}"]
        if not runs:
            continue
        # One table per set; overhead against the healthy baseline of the same point
        tables = []
        for point, _ in cfg["points"]:
            point_runs = [r for i, r in enumerate(runs) if i % 2 == 1 and r.split("=", 1)[0].endswith(f"_{point}")]
            if not point_runs:
                continue
            argv = [sys.executable, str(METRICS), "--baseline", f"{cfg['baseline']}_{point}",
                    "--csv", str(set_root / f"summary_{point}.csv")]
            for r in point_runs:
                argv += ["--run", r]
            tables.append(f"### {name} {point}\n\n" + subprocess.run(argv, capture_output=True, text=True).stdout)
        (set_root / "summary.md").write_text(f"# Sweep {name}\n\n" + "\n".join(tables))
        print((set_root / "summary.md").read_text(), flush=True)


if __name__ == "__main__":
    main()
