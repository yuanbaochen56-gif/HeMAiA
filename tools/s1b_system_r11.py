"""R11 runner: scenes 6 and 7 under the frozen r9 gate profile.

The R10 runner, unchanged, pointed at R11: it runs s1b_stage0_shadow_on and
s1b_invalid_status in frozen order, loads both per-configuration references
and the R9 injected-task budgets, and re-checks the plan, the tools and the
frozen profile hash before every scene. Only the judge differs: the r9 profile
adds decision 3 on the declared release row. First failure stops.
"""
from pathlib import Path

import s1b_system_r10 as base
import s1b_profile_r11 as profiles

REUSED_R8 = base.REUSED_R8
REUSED_R9 = base.REUSED_R9
REUSED_R10 = ("s1b_stage0_primary_off",)
REUSED = REUSED_R8 + REUSED_R9 + REUSED_R10
PLANNED = ("s1b_stage0_shadow_on", "s1b_invalid_status")


def configure(E, ROOT, PRED):
    base.E, base.ROOT, base.PRED = Path(E), Path(ROOT), Path(PRED)
    base.REUSED, base.PLANNED, base.profiles = REUSED, PLANNED, profiles


def scene_plan(sealed):
    base.REUSED, base.PLANNED = REUSED, PLANNED
    return base.scene_plan(sealed)


def judged_run(name, scene, log, uart, observer, sealed, reference, record, profile):
    return profiles.judge(name, scene, log, uart, observer, sealed, reference, record, profile)


def main(**kwargs):
    if base.profiles is not profiles or base.PLANNED != PLANNED or base.REUSED != REUSED:
        raise RuntimeError("R11 runner not configured")
    return base.main(**kwargs)
