// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include "libbingo/bingo_api.h"

// Test-only host node, ordered after a remapped copy and before the next copy.
// CLEAR is a held mask, not a write-one pulse. Keep it set through the run so
// later late beats cannot park the recovered slot again.
uint64_t __host_bingo_kernel_risk_clear(void *arg) {
    __host_bingo_kernel_dummy_args_t *args = arg;
    uint32_t mask = (uint32_t)args->dummy_input;
    uintptr_t risk_addr = (uintptr_t)chiplet_addr_transform(
        (uint64_t)quad_ctrl_bingo_risk_addr());
    uintptr_t clear_addr = (uintptr_t)chiplet_addr_transform(
        (uint64_t)quad_ctrl_bingo_risk_clear_addr());
    uint32_t before = readw(risk_addr);
    writew(mask, clear_addr);
    asm volatile("fence" ::: "memory");
    uint32_t held = readw(clear_addr);
    uint32_t after = readw(risk_addr);
    printf_safe("[RiskChain] CLEAR before=0x%x mask=0x%x held=0x%x after=0x%x\r\n",
                before, mask, held, after);
    uint64_t result = (before == mask && held == mask && after == 0)
                          ? BINGO_RET_SUCC : BINGO_RET_FAIL;
    bingo_kernel_scratchpad_t *sp =
        (bingo_kernel_scratchpad_t *)(uintptr_t)args->scratchpad_ptr;
    sp->return_value = result;
    sp->num_return_values = 0;
    return result;
}
