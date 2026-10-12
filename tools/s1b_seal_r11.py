"""Hash-only seal of evidence/GateS1bR11, results/GateS1bR11 and every reused input.

The R9 seal rule, pointed at R11: run after RESULT.md is written; manifest.json
and manifest.sha256 are the only files excluded. The reused R8/R9/R10 runs, the
R9 budgets, the r8 profile and both frozen references are bound by hash through
the R11 plan.
"""
from pathlib import Path

import s1b_seal_r9 as seal

W = Path(__file__).resolve().parents[1]


def main():
    seal.E = W / "evidence/GateS1bR11"
    seal.RESULTS = W / "results/GateS1bR11"
    seal.main()


if __name__ == "__main__":
    main()
