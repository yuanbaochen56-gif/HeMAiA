// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
#pragma once

#if defined(BINGO_SDC) && BINGO_TEST_CFG
#include <stddef.h>
#include "../../../host/runtime/libbingo/include/libbingo/device_kernel_args.h"

extern uint32_t __snax_bingo_kernel_idma_1d_copy(void *arg);
extern uint32_t __snax_bingo_kernel_int32_add(void *arg);

typedef struct {
    uint32_t (*kernel)(void *);
    size_t output_lo;
    size_t output_hi;
    size_t count;
    uint32_t element_bytes;
    uint32_t use_dma;
} bingo_sdc_output_spec_t;

static const bingo_sdc_output_spec_t bingo_sdc_output_table[] = {
    {__snax_bingo_kernel_idma_1d_copy,
     offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, dst_addr_lo),
     offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, dst_addr_hi),
     offsetof(__snax_bingo_kernel_idma_1d_copy_args_t, size), 1, 1},
    {__snax_bingo_kernel_int32_add,
     offsetof(__snax_bingo_kernel_int32_add_args_t, c_addr), SIZE_MAX,
     offsetof(__snax_bingo_kernel_int32_add_args_t, num_elements), 4, 0},
};

static inline const bingo_sdc_output_spec_t *bingo_sdc_output_spec(uintptr_t kernel) {
    for (size_t i = 0; i < sizeof(bingo_sdc_output_table) / sizeof(bingo_sdc_output_table[0]); ++i) {
        if ((uintptr_t)bingo_sdc_output_table[i].kernel == kernel) return &bingo_sdc_output_table[i];
    }
    return NULL;
}

static inline void bingo_sdc_inject(uint32_t gid, uintptr_t kernel, uintptr_t arg,
                                   const volatile bingo_test_cfg_t *cfg) {
    if (gid != cfg->fault_sdc_gid ||
        (cfg->fault_sdc_cluster != UINT32_MAX && cfg->fault_sdc_cluster != snrt_cluster_idx()) ||
        (cfg->fault_sdc_core != UINT32_MAX && cfg->fault_sdc_core != snrt_cluster_core_idx()) ||
        cfg->fault_sdc_mask == 0) return;
    const bingo_sdc_output_spec_t *spec = bingo_sdc_output_spec(kernel);
    if (!spec) {
        printf_safe("[BINGO_SDC_WARN] gid=%u unsupported output kernel\r\n", gid);
        return;
    }
    const uint8_t *args = (const uint8_t *)arg;
    uint64_t output = *(const uint32_t *)(args + spec->output_lo);
    if (spec->output_hi != SIZE_MAX) {
        output |= (uint64_t)*(const uint32_t *)(args + spec->output_hi) << 32;
    }
    uint64_t length = (uint64_t)*(const uint32_t *)(args + spec->count) * spec->element_bytes;
    uint32_t offset = cfg->fault_sdc_offset;
    if (offset >= length || output > UINT64_MAX - offset) {
        printf_safe("[BINGO_SDC_WARN] gid=%u offset=%u outside output\r\n", gid, offset);
        return;
    }
    uint8_t mask = (uint8_t)cfg->fault_sdc_mask;
    if (spec->use_dma) {
        uint32_t byte = 0;
        snrt_dma_start_1d_wideptr((uint64_t)(uintptr_t)&byte, output + offset, 1);
        snrt_dma_wait_all();
        byte ^= mask;
        snrt_dma_start_1d_wideptr(output + offset, (uint64_t)(uintptr_t)&byte, 1);
        snrt_dma_wait_all();
    } else {
        volatile uint8_t *byte = (volatile uint8_t *)(uintptr_t)(output + offset);
        *byte ^= mask;
    }
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    printf_safe("[BINGO_SDC_INJECT] gid=%u cluster=%u core=%u offset=%u mask=%u\r\n",
                gid, snrt_cluster_idx(), snrt_cluster_core_idx(), offset, (uint32_t)mask);
}
#endif
