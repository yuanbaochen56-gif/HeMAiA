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

#define BINGO_HW_HEARTBEAT_STR_(x) #x
#define BINGO_HW_HEARTBEAT_STR(x) BINGO_HW_HEARTBEAT_STR_(x)

// The written value is ignored by the watchdog (reserved for progress counters).
static inline void bingo_hw_manager_heartbeat(uint32_t value) {
    asm volatile("csrw " BINGO_HW_HEARTBEAT_STR(BINGO_HW_MANAGER_HEARTBEAT_CSR) ", %0"
                 :
                 : "r"(value));
}
