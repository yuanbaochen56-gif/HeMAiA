// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include "libbingo/bingo_api.h"
#include "hemaia_clk_rst_controller.h"

// The division the host clock (domain 0) starts with (occamy_chip
// HeMAiADefaultDivision[0]): reprogramming it changes nothing while the
// controller takes the host's byte.
#define CLK_RETUNE_HOST_DIVISION 14

// Test-only host node: reprogram the host clock RETUNE times with
// enable_clk_domain (read the packed division register of domains 0-3, change
// byte 0, write it back, set valid bit 0), while the device chain makes the
// bingo PM change the cluster clocks (domains 1 and 2) in the same word.
uint64_t __host_bingo_kernel_clk_retune(void *arg) {
    __host_bingo_kernel_dummy_args_t *args = arg;
    uint32_t count = (uint32_t)args->dummy_input;
    for (uint32_t i = 0; i < count; i++) enable_clk_domain(0, CLK_RETUNE_HOST_DIVISION);
    printf_safe("[CLK_RETUNE] host clock reprogrammed %u times\r\n", count);
    bingo_kernel_scratchpad_t *sp = (bingo_kernel_scratchpad_t *)(uintptr_t)args->scratchpad_ptr;
    sp->return_value = BINGO_RET_SUCC;
    sp->num_return_values = 0;
    return BINGO_RET_SUCC;
}

// Test-only host node after both: the packed division register of domains
// 0-3 and the per-domain division registers of the two clusters
uint64_t __host_bingo_kernel_clk_report(void *arg) {
    __host_bingo_kernel_dummy_args_t *args = arg;
    uintptr_t base = (uintptr_t)get_current_chip_baseaddress() | HEMAIA_CLK_RST_CONTROLLER_BASE_ADDR;
    uint32_t packed = *(volatile uint32_t *)(base + HEMAIA_CLK_RST_CONTROLLER_CLOCK_DIVISION_REGISTER_C0_C3_REG_OFFSET);
    uint32_t own1 = *(volatile uint32_t *)(base + HEMAIA_CLK_RST_CONTROLLER_DOMAIN_DIVISION_C1_REG_OFFSET);
    uint32_t own2 = *(volatile uint32_t *)(base + HEMAIA_CLK_RST_CONTROLLER_DOMAIN_DIVISION_C2_REG_OFFSET);
    printf_safe("[CLK_RETUNE] packed C0-C3=0x%08x domain C1=%u C2=%u\r\n", packed, own1, own2);
    bingo_kernel_scratchpad_t *sp = (bingo_kernel_scratchpad_t *)(uintptr_t)args->scratchpad_ptr;
    sp->return_value = BINGO_RET_SUCC;
    sp->num_return_values = 0;
    return BINGO_RET_SUCC;
}
