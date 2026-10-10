// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#ifdef BINGO_SDC
// Included by snax_kernel_lib.h after macros.h. DMA preserves full addresses
// on DM cores. Plain-core int32 tasks use CPU-addressable single-chip buffers.
SNAX_LIB_DEFINE uint32_t __snax_bingo_kernel_sdc_compare(void *arg) {
    BINGO_SW_GUARD_CHECK(arg, __snax_bingo_kernel_sdc_compare_args_t);
    const __snax_bingo_kernel_sdc_compare_args_t *a =
        (const __snax_bingo_kernel_sdc_compare_args_t *)arg;
    uint64_t primary = ((uint64_t)a->primary_addr_hi << 32) | a->primary_addr_lo;
    uint64_t shadow = ((uint64_t)a->shadow_addr_hi << 32) | a->shadow_addr_lo;
    uint64_t status = ((uint64_t)a->status_addr_hi << 32) | a->status_addr_lo;
    uint32_t result = 0;
    if (snrt_is_dm_core()) {
        uint8_t left[64] __attribute__((aligned(8)));
        uint8_t right[64] __attribute__((aligned(8)));
        for (uint32_t offset = 0; offset < a->size && result == 0;) {
            uint32_t size = a->size - offset;
            if (size > sizeof(left)) size = sizeof(left);
            snrt_dma_start_1d_wideptr((uint64_t)(uintptr_t)left, primary + offset, size);
            snrt_dma_start_1d_wideptr((uint64_t)(uintptr_t)right, shadow + offset, size);
            snrt_dma_wait_all();
            for (uint32_t i = 0; i < size; ++i) {
                if (left[i] != right[i]) {
                    result = 1 + offset + i;
                    break;
                }
            }
            offset += size;
            bingo_hw_manager_heartbeat(1);
        }
        snrt_dma_start_1d_wideptr(status, (uint64_t)(uintptr_t)&result, sizeof(result));
        snrt_dma_wait_all();
    } else {
        const volatile uint8_t *left = (const volatile uint8_t *)(uintptr_t)primary;
        const volatile uint8_t *right = (const volatile uint8_t *)(uintptr_t)shadow;
        for (uint32_t i = 0; i < a->size; ++i) {
            if (left[i] != right[i]) {
                result = 1 + i;
                break;
            }
            if ((i & 63) == 63) bingo_hw_manager_heartbeat(1);
        }
        *(volatile uint32_t *)(uintptr_t)status = result;
    }
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    bingo_kernel_scratchpad_t *sp = BINGO_GET_SP(arg, __snax_bingo_kernel_sdc_compare_args_t);
    sp->return_value = result;
    sp->num_return_values = 1;
    printf_safe("[BINGO_SDC] cluster=%u core=%u status_lo=0x%x result=%u\r\n",
                snrt_cluster_idx(), snrt_cluster_core_idx(), a->status_addr_lo, result);
    return BINGO_RET_SUCC;
}
#endif
