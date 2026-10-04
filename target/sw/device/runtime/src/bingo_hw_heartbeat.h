// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Heartbeat for the bingo HW manager watchdog.
//
// While a core runs a task, the watchdog in bingo_hw_manager expects a heartbeat
// at least every WatchdogHeartbeatTimeoutCycles quad-clock cycles (cfg
// s1_quadrant.bingo_watchdog_timeout_cycles), otherwise it reports the core as
// dead_suspect. Idle cores need no heartbeat. Long waits inside kernels should
// call bingo_hw_manager_heartbeat() in their polling loops.
//
// On HeMAiA the heartbeat is a write to the ready-queue CSR 0x5fe
// (CsrHeartbeatAddr in occamy_quad_ctrl): snax_intf_translator only forwards
// CSRs 0x5fe/0x5ff to the bingo manager, a write to 0x5fd would reach the
// core's accelerator CSRs instead.

#pragma once

#include <stdint.h>

#define BINGO_HW_MANAGER_HEARTBEAT_CSR 0x5fe
// Report a real exit after done; a new ready read clears the manager's exit bit.
#define BINGO_HW_HEARTBEAT_EXIT (UINT32_C(1) << 31)

#define BINGO_HW_HEARTBEAT_STR_(x) #x
#define BINGO_HW_HEARTBEAT_STR(x) BINGO_HW_HEARTBEAT_STR_(x)

// The watchdog ignores the value. Bit 31 reports exit to the control plane;
// ordinary heartbeats must leave it clear.
static inline void bingo_hw_manager_heartbeat(uint32_t value) {
#ifdef BINGO_WD_NO_HEARTBEAT
    // Only for C2 measurement; must use the default watchdog (10M) configuration.
    // Keep A1's EXIT-bit report even when ordinary heartbeats are disabled.
    if (!(value & BINGO_HW_HEARTBEAT_EXIT)) return;
#endif
    asm volatile("csrw " BINGO_HW_HEARTBEAT_STR(BINGO_HW_MANAGER_HEARTBEAT_CSR) ", %0"
                 :
                 : "r"(value));
}
