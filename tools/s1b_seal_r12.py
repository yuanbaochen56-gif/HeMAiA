"""Hash-only seal of evidence/GateS1bR12 and every reused input.

The R9 seal rule, pointed at R12: run after RESULT.md is written; manifest.json
and manifest.sha256 are the only files excluded. R12 runs no simulation, so
there is no results/GateS1bR12; the retained R11 probe run, the reused
R8/R9/R10/R11 runs, the r8 and r9 profiles, the R9 budgets and both frozen
references are bound by hash through the R12 plan.
"""
from pathlib import Path

import s1b_seal_r9 as seal

W = Path(__file__).resolve().parents[1]


def main():
    seal.E = W / "evidence/GateS1bR12"
    seal.RESULTS = W / "results/GateS1bR12"
    if seal.RESULTS.exists():
        raise SystemExit("R12 runs no simulation; results/GateS1bR12 must not exist")
    seal.main()


if __name__ == "__main__":
    main()
