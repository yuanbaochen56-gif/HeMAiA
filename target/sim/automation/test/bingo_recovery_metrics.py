#!/usr/bin/env python3
r"""
Bingo recovery metrics from HeMAiA system-sim logs
==================================================
Reads the ``sim_run.log`` of bingo watchdog scenarios
(``3_start_bingo_watchdog_sim.py``) and reports, per run:

* ``eoc_ms``           end of computation ("All chips finished successfully"), or none
* ``suspect_ms``       first dead_suspect of a core   ``fence_ms``  its fence
* ``move_ms``          first migration step after the fence ([BINGO_REPLAY] / [BINGO_EXPORT])
* ``retired_ms``       the fenced core's migration is complete ([BINGO_RETIRED])
* ``recovery_ms``      eoc - fence
* ``overhead_ms``      eoc - eoc of the baseline run (``--baseline``)
* ``lvl_<L>_pct``      share of the fence -> eoc window (or the whole run without a fence)
                       spent at power level L of domain 1 ([BINGO_PM] lines)
* ``cycles_rel``       clock cycles of domain 1 in that window, sum(time / level), relative to
                       running the whole window at the normal level (a dynamic-power proxy;
                       the levels are clock dividers)

The window starts at the fence (faulty runs) or at the first [BINGO_PM] line (healthy runs).

Usage
-----
    python3 bingo_recovery_metrics.py --run s6=/path/to/s6 --run s7=/path/to/s7 \
        --baseline s6 [--normal-level 6] [--csv out.csv]

A run path is a ``sim_run.log``, or a directory searched for ``*/bin/sim_run.log``
(a scenario directory of the driver's output root).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

EOC_RE = re.compile(r"All chips finished successfully at (\d+)")
WD_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=\d+ core=\d+ cluster=\d+ dead_suspect=(\d)(?: fenced=(\d))?")
MOVE_RE = re.compile(r"\[BINGO_(?:REPLAY|EXPORT)\] (\d+) ")
RETIRED_RE = re.compile(r"\[BINGO_RETIRED\] (\d+) ")
PM_RE = re.compile(r"\[BINGO_PM\] (\d+) domain=1 level=(\d+)")
PS_PER_MS = 1e9  # sim times are in ps


def find_log(path: Path) -> Path:
    if path.is_file():
        return path
    logs = sorted(path.glob("*/bin/sim_run.log")) + sorted(path.glob("bin/sim_run.log"))
    if not logs:
        sys.exit(f"no sim_run.log under {path}")
    return logs[0]


def metrics(log: Path, normal_level: int) -> Dict[str, object]:
    text = log.read_text(errors="replace")
    m: Dict[str, object] = {}
    eoc = EOC_RE.search(text)
    m["eoc_ms"] = int(eoc.group(1)) / PS_PER_MS if eoc else None
    suspects = [int(t) for t, s, f in WD_RE.findall(text) if s == "1" and f != "1"]
    fences = [int(t) for t, s, f in WD_RE.findall(text) if f == "1"]
    m["suspect_ms"] = suspects[0] / PS_PER_MS if suspects else None
    m["fence_ms"] = fences[0] / PS_PER_MS if fences else None
    fence = fences[0] if fences else None
    moves = [int(t) for t in MOVE_RE.findall(text) if fence is not None and int(t) >= fence]
    m["move_ms"] = moves[0] / PS_PER_MS if moves else None
    retired = [int(t) for t in RETIRED_RE.findall(text)]
    m["retired_ms"] = retired[0] / PS_PER_MS if retired else None
    m["recovery_ms"] = (m["eoc_ms"] - m["fence_ms"]) if (m["eoc_ms"] is not None and fence is not None) else None

    # Power level of domain 1 over the window
    pm = [(int(t), int(l)) for t, l in PM_RE.findall(text)]
    end = int(eoc.group(1)) if eoc else None   # no EOC (stuck / hung run): no power window
    start = fence if fence is not None else (pm[0][0] if pm else None)
    shares: Dict[int, float] = {}
    if pm and start is not None and end is not None and end > start:
        level = None
        last = start
        for t, l in pm:
            if t <= start:
                level = l
                continue
            if t > end:
                break
            if level is not None:
                shares[level] = shares.get(level, 0) + (t - last)
            level, last = l, t
        if level is not None:
            shares[level] = shares.get(level, 0) + (end - last)
        window = end - start
        m["window_ms"] = window / PS_PER_MS
        for l, d in sorted(shares.items()):
            m[f"lvl_{l}_pct"] = round(100.0 * d / window, 1)
        m["cycles_rel"] = round(sum(d / l for l, d in shares.items()) / (window / normal_level), 3)
    return m


def fmt(v: object) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="append", required=True, metavar="LABEL=PATH",
                        help="a run: label and sim_run.log or scenario directory (repeatable)")
    parser.add_argument("--baseline", help="label of the run whose EOC is the reference for overhead_ms")
    parser.add_argument("--normal-level", type=int, default=6, help="normal power level (clock divider)")
    parser.add_argument("--csv", type=Path, help="also write the table as CSV")
    args = parser.parse_args()

    rows: List[Dict[str, object]] = []
    for spec in args.run:
        label, _, path = spec.partition("=")
        row: Dict[str, object] = {"run": label}
        row.update(metrics(find_log(Path(path)), args.normal_level))
        rows.append(row)
    base: Optional[float] = None
    if args.baseline:
        base = next((r["eoc_ms"] for r in rows if r["run"] == args.baseline), None)
    for r in rows:
        r["overhead_ms"] = (r["eoc_ms"] - base) if (base is not None and r.get("eoc_ms") is not None) else None

    fixed = ["run", "eoc_ms", "overhead_ms", "suspect_ms", "fence_ms", "move_ms", "retired_ms",
             "recovery_ms", "window_ms", "cycles_rel"]
    levels = sorted({k for r in rows for k in r if k.startswith("lvl_")}, key=lambda k: int(k.split("_")[1]))
    cols = fixed + levels
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(fmt(r.get(c)) for c in cols) + " |")
    if args.csv:
        with args.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c) for c in cols})


if __name__ == "__main__":
    main()
