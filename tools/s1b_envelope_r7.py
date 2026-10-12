"""The sole timing-gate change authorized on 2026-10-11 at 18:30 CEST."""
import copy
from decimal import Decimal

HEALTHY_LIMIT_US = 8700
LEGACY_HEALTHY_ESTIMATE_US = 8592
MEASURED_REFERENCE_PS = 8_613_922_000
DECISION = "2026-10-11 18:30 CEST"


def healthy_rebase_record():
    measured = Decimal(MEASURED_REFERENCE_PS) / 1_000_000
    exact_target = measured * Decimal("1.01")
    return dict(
        user_decision=DECISION,
        measured_reference_ps=MEASURED_REFERENCE_PS,
        measured_reference_us=str(measured),
        measurement="task10 entry DISPATCH to finite simulator end; padded SDC ON, recovery OFF",
        requested_margin_percent="1.0",
        exact_one_percent_target_us=str(exact_target),
        approved_limit_us="8700.000",
        approved_limit_ps=HEALTHY_LIMIT_US * 1_000_000,
        actual_margin_percent=str((Decimal(HEALTHY_LIMIT_US) / measured - 1) * 100),
        rounding_shortfall_us=str(exact_target - HEALTHY_LIMIT_US),
        rationale=("User explicitly chose 8700.000 us by rounding the measured +1% target down. "
                   "The exact target is 8700.06122 us; the resulting margin is slightly below 1%, "
                   "not >=1%. Apply the explicit numeric limit without changing other gates."),
        superseded_limit_us=LEGACY_HEALTHY_ESTIMATE_US,
        superseded_basis="Unmeasured R6 serial planning estimate; not a measured padded reference",
        legacy_tooling=["tools/s1b_proposals_r6.py", "tools/s1b_seal_r6.py",
                        "tools/s1b_freeze_r1.py", "tools/s1b_stop_release_r1.py"],
        unchanged_mismatch_envelopes_us={"1": 9616, "2": 10640})


def rebase_healthy_budget(budgets):
    """Copy the reviewed budget; change only the healthy limit and its provenance."""
    if budgets["normal_all_healthy_envelope"] not in (LEGACY_HEALTHY_ESTIMATE_US, HEALTHY_LIMIT_US):
        raise ValueError("Unexpected healthy envelope: STOP_USER")
    result = copy.deepcopy(budgets)
    result["normal_all_healthy_envelope"] = HEALTHY_LIMIT_US
    result["healthy_envelope_rebase"] = healthy_rebase_record()
    return result


def success_envelope_us(budgets, mismatch_count):
    if type(mismatch_count) is not int or not 0 <= mismatch_count <= 2:
        raise ValueError("Unbound mismatch count: STOP_USER")
    if not mismatch_count:
        return budgets["normal_all_healthy_envelope"]
    # The healthy rebase must not also raise the existing mismatch gates.
    delta = (budgets["kernel_bounds"]["resolver_mismatch_including_report"]
             - budgets["kernel_bounds"]["resolver_healthy_including_report"])
    return budgets["normal_two_mismatch_envelope"] - (2 - mismatch_count) * delta
