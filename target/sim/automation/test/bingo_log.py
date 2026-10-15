"""Line formats of the bingo simulation logs, defined once for the driver and the checkers.

sim_run.log: the [BINGO_*] lines of bingo_hw_manager_top and occamy_quad_ctrl
(simulation only), the [EVLOG_BEGIN] marker and the end-of-run line of the
testbench. UART: the host's status and check lines. Every pattern is the exact
text the modules used before they shared it.
"""
import re

# fenced= only exists since the replay support; older RTL prints dead_suspect only.
WD_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dead_suspect=(\d)(?: fenced=(\d))?")
# (the same line, current RTL only)
WD_FULL_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dead_suspect=(\d+) fenced=(\d+)")
REMAP_RE = re.compile(r"\[BINGO_REMAP\].*")
REPLAY_RE = re.compile(r"\[BINGO_REPLAY\].*")
# Parsed forms of the remap / replay / retire / fence-drop lines
REMAP_FIELDS_RE = re.compile(r"\[BINGO_REMAP\] \d+ chip=(\d+) task=(\d+) logical_core=(\d+) -> physical_core=(\d+) cluster=(\d+)")
REPLAY_FIELDS_RE = re.compile(r"\[BINGO_REPLAY\] \d+ chip=(\d+) task=(\d+) type=\d+ logical_core=(\d+) from=(\d+) to=(\d+) cluster=(\d+) to_cluster=(\d+)")
RETIRED_RE = re.compile(r"\[BINGO_RETIRED\] \d+ chip=(\d+) core=(\d+) cluster=(\d+)")
EXIT_ABSORB_RE = re.compile(r"\[BINGO_EXIT_ABSORB\] \d+ chip=(\d+) task=(\d+) logical_core=(\d+) "
                            r"logical_cluster=(\d+) core=(\d+) cluster=(\d+)")
FENCE_DROP_RE = re.compile(r"\[BINGO_FENCE\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) dropped done task=(\d+)")
STUCK_RE = re.compile(r"\[BINGO_REPLAY_STUCK\].*")
STUCK_TIME_RE = re.compile(r"\[BINGO_REPLAY_STUCK\] (\d+) ")
PARK_RE = re.compile(r"\[BINGO_PARK\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) (HOLD|FAIL|dropped, fenced|PARKED -> core=(\d+) cluster=(\d+)|UNPARKED|UNPARK \(\d+ tasks left elsewhere\))")
ASSERT_RE = re.compile(r"\[BINGO_ASSERT\].*")
CSR_RE = re.compile(r"\[BINGO_CSR\].*")
# A bus error of a manager master (descriptor fetch, PM): bingo_hw_manager_top bus_error_o
BUS_ERROR_RE = re.compile(r"\[BINGO_BUS_ERROR\].*")
STATUS_RE = re.compile(r"\[BINGO_STATUS\] \d+ chip=\d+ (.*)")
STATUS_FENCED_RE = re.compile(r"\[BINGO_STATUS\] \d+ chip=\d+ .*fenced=0x([0-9a-fA-F]+)")
# Level 3 (bingo_hw_manager_top / occamy_quad_ctrl remote link logs)
EXPORT_RE = re.compile(r"\[BINGO_EXPORT\] \d+ chip=(\d+) task=(\d+) type_id=(\d+) proxy_slot=(\d+)")
IMPORT_RE = re.compile(r"\[BINGO_IMPORT\] \d+ chip=(\d+) task=(\d+) from chip=(\d+) proxy_slot=(\d+) -> core=(\d+) cluster=(\d+)")
RDONE_OUT_RE = re.compile(r"\[BINGO_REMOTE_DONE_OUT\] \d+ chip=(\d+) task=(\d+) -> chip=(\d+) proxy_slot=(\d+)")
RDONE_IN_RE = re.compile(r"\[BINGO_REMOTE_DONE_IN\] \d+ chip=(\d+) task=(\d+) proxy_slot=(\d+)")
REJECT_OUT_RE = re.compile(r"\[BINGO_REMOTE_REJECT_OUT\] \d+ chip=(\d+) task=(\d+) -> chip=(\d+) proxy_slot=(\d+)")
REJECT_IN_RE = re.compile(r"\[BINGO_REMOTE_REJECT_IN\] \d+ chip=(\d+) task=(\d+) proxy_slot=(\d+)")
RLINK_RE = re.compile(r"\[BINGO_RLINK_(TX|RX)_(AW|W|B)\] \d+ chip=(\d+) (addr|data|resp)=(0x[0-9a-fA-F]+|\d+)")
# Precursors, CERF, scheduling and power
LATE_RE = re.compile(r"\[BINGO_LATE\] \d+ core=(\d+) cluster=(\d+)")
RISK_RE = re.compile(r"\[BINGO_RISK\] \d+ core=(\d+) cluster=(\d+) at risk")
CERF_FB_RE = re.compile(r"\[BINGO_CERF_FB\] (\d+) type (\d+) clear g(\d+) set g(\d+)")
DISPATCH_RE = re.compile(r"\[BINGO_DISPATCH\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
DONE_RE = re.compile(r"\[BINGO_DONE\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
TYPE_CONFIRM_RE = re.compile(
    r"\[BINGO_TYPE_CONFIRM\] (\d+) core=(\d+) cluster=(\d+) type=(\d+) threshold=(\d+)")
PM_RE = re.compile(r"\[BINGO_PM\] (\d+) domain=(\d+) level=(\d+)")
# (cluster domain 1: the victim's cluster in the hemaia_ci scenes)
PM_DOMAIN1_RE = re.compile(r"\[BINGO_PM\] (\d+) domain=1 level=(\d+)")


def pm_domain_re(domain: int) -> "re.Pattern":
    """[BINGO_PM] lines of one power domain (libbingo: cluster c is domain 1 + c)."""
    return re.compile(rf"\[BINGO_PM\] (\d+) domain={domain} level=(\d+)")
RECOVERY_HOLD_RE = re.compile(r"\[BINGO_RECOVERY_HOLD\] (\d+) core=(\d+) cluster=(\d+) active=([01])")
EVLOG_BEGIN_RE = re.compile(r"\[EVLOG_BEGIN\] (\d+)")
EOC_RE = re.compile(r"All chips finished successfully at (\d+)")
# UART (host)
CHECK_RE = re.compile(r"Check \[([^\]]*)\]: (PASS|FAIL)")
# (any verdict word, not only PASS / FAIL)
CHECK_ANY_RE = re.compile(r"Check \[([^\]]*)\]:\s*(\S+)")
HOST_STATUS_RE = re.compile(r"\[Host\] Bingo status: replay_stuck=(\d+) remote_done_mismatch=(\d+) "
                            r"link_error=(\d+) fenced=0x([0-9a-fA-F]+)")
C6_LINE_RE = re.compile(r"\[Host\] C6 cerf_fb_mode=(\d+) poll_cycles=(\d+) "
                        r"sw_writes=(\d+) sw_evt=0x([0-9a-fA-F]+)")
