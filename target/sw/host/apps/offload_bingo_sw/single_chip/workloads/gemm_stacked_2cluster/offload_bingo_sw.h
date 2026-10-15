// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// The stacked GEMM of the hardware-manager workload
// offload_bingo_hw/single_chip/workloads/gemm_stacked_2cluster on the software
// runtime, with the same core-level kernels on the same graph and data. Per
// cluster c (12 tasks in all):
//
//   Load_A1 + Load_B1 -> Gemm_D1 -> Load_B2 ; Gemm_D1 + Load_B2 -> Gemm_D2 -> Store_D2
//
// The copies run __snax_bingo_kernel_idma_1d_copy on the DM core (core 1), the
// GEMMs __snax_bingo_kernel_gemm_full on core 0. The software runtime calls a
// kernel on every core of the cluster, so each task calls
// __snax_kernel_core_call, which runs the core-level kernel on its core only.
// Kernel arguments, core-call arguments and scratchpads are in the cluster's
// L1, as in the hardware workload; the runtime's task records are in L2. The
// host checks the whole D2 of both clusters after the schedule.

#pragma once
#include "libbingo/bingo_api.h"
#include "host.h"
#include "gemm_data.h"
#include "gemm_dims.h"

#ifndef BINGO_SW_CORE_CALL
#error "gemm_stacked_2cluster calls core-level kernels: build it with -DBINGO_SW_CORE_CALL"
#endif

#define GS_GEMM_CORE 0
#define GS_DMA_CORE 1
#define GS_TASKS_PER_CLUSTER 6

typedef struct {
    uint64_t a1, b1, b2, d1, d2;
} gs_l1_buffers_t;

static uint64_t gs_d2_l3[2];
static uint32_t gs_call_fn, gs_copy_fn, gs_gemm_fn;

static bingo_task_t *gs_task(uint32_t cluster, uint32_t core, uint32_t fn, void *inner) {
    __snax_kernel_core_call_args_t *call = (__snax_kernel_core_call_args_t *)(uintptr_t)
        bingo_l1_alloc(0, cluster, sizeof(__snax_kernel_core_call_args_t));
    call->core_idx = core;
    call->fn = fn;
    call->inner_args = (uint32_t)(uintptr_t)inner;
    return bingo_task_create(gs_call_fn, (uint32_t)(uintptr_t)call, 0, cluster);
}

static bingo_task_t *gs_copy(uint32_t cluster, uint64_t src, uint64_t dst, uint32_t size,
                             bingo_kernel_scratchpad_t *sp) {
    __snax_bingo_kernel_idma_1d_copy_args_t *args = (__snax_bingo_kernel_idma_1d_copy_args_t *)(uintptr_t)
        bingo_l1_alloc(0, cluster, sizeof(__snax_bingo_kernel_idma_1d_copy_args_t));
    args->src_addr_lo = (uint32_t)src;
    args->src_addr_hi = (uint32_t)(src >> 32);
    args->dst_addr_lo = (uint32_t)dst;
    args->dst_addr_hi = (uint32_t)(dst >> 32);
    args->size = size;
    args->gating_sp_addr = 0;
    args->cond_node_index = 0;
    args->scratchpad_ptr = (uint32_t)(uintptr_t)sp;
    return gs_task(cluster, GS_DMA_CORE, gs_copy_fn, args);
}

static bingo_task_t *gs_gemm(uint32_t cluster, uint64_t a, uint64_t b, uint64_t d, uint32_t m,
                             uint32_t k, uint32_t n, bingo_kernel_scratchpad_t *sp) {
    __snax_bingo_kernel_gemm_full_args_t *args = (__snax_bingo_kernel_gemm_full_args_t *)(uintptr_t)
        bingo_l1_alloc(0, cluster, sizeof(__snax_bingo_kernel_gemm_full_args_t));
    args->input_A_addr = (uint32_t)a;
    args->input_B_addr = (uint32_t)b;
    args->input_C_addr = 0;
    args->output_D_addr = (uint32_t)d;
    args->M = m;
    args->K = k;
    args->N = n;
    args->array_shape_idx = GS_ARRAYSHAPEIDX;
    args->transpose_A = GS_TRANSPOSEA;
    args->transpose_B = GS_TRANSPOSEB;
    args->accumPrevC = GS_ACCUMPREVC;
    args->quantization_enable = 0;
    args->shift_i = 0;
    args->multiplier_i = 0;
    args->input_zp_i = 0;
    args->output_zp_i = 0;
    args->int32tofp16_enable = 0;
    args->int4_a_enable = 0;
    args->int4_b_enable = 0;
    args->gating_sp_addr = 0;
    args->cond_node_index = 0;
    args->scratchpad_ptr = (uint32_t)(uintptr_t)sp;
    return gs_task(cluster, GS_GEMM_CORE, gs_gemm_fn, args);
}

uint32_t __workload_gemm_stacked_2cluster(bingo_task_t **task_list) {
    check_kernel_tab_ready();
    printf_safe("Chip(%x, %x): [Host] Preparing GEMM Stacked 2-Cluster Workload (core-level kernels)\r\n",
                get_current_chip_loc_x(), get_current_chip_loc_y());
    gs_call_fn = get_device_function("__snax_kernel_core_call");
    gs_copy_fn = get_device_function("__snax_bingo_kernel_idma_1d_copy");
    gs_gemm_fn = get_device_function("__snax_bingo_kernel_gemm_full");
    if (gs_call_fn == SNAX_SYMTAB_END_FN_ADDR || gs_copy_fn == SNAX_SYMTAB_END_FN_ADDR ||
        gs_gemm_fn == SNAX_SYMTAB_END_FN_ADDR) {
        printf_safe("Error: Kernel symbol lookup failed!\r\n");
        return 0;
    }
    // Buffers in the order of the hardware workload's allocations
    gs_l1_buffers_t buf[2];
    for (uint32_t c = 0; c < 2; c++) buf[c].a1 = bingo_l1_alloc(0, c, GS_A1_SIZE);
    for (uint32_t c = 0; c < 2; c++) buf[c].b1 = bingo_l1_alloc(0, c, GS_B1_SIZE);
    for (uint32_t c = 0; c < 2; c++) buf[c].b2 = bingo_l1_alloc(0, c, GS_B2_SIZE);
    for (uint32_t c = 0; c < 2; c++) buf[c].d1 = bingo_l1_alloc(0, c, GS_D1_SIZE);
    for (uint32_t c = 0; c < 2; c++) buf[c].d2 = bingo_l1_alloc(0, c, GS_D2_SIZE);
    for (uint32_t c = 0; c < 2; c++) gs_d2_l3[c] = bingo_l3_alloc(0, GS_D2_SIZE);
    bingo_kernel_scratchpad_t *sp[2][GS_TASKS_PER_CLUSTER];
    for (uint32_t c = 0; c < 2; c++)
        for (uint32_t i = 0; i < GS_TASKS_PER_CLUSTER; i++)
            sp[c][i] = (bingo_kernel_scratchpad_t *)(uintptr_t)
                bingo_l1_alloc(0, c, BINGO_KERNEL_SCRATCHPAD_SIZE);
    uint64_t a1 = chiplet_addr_transform((uint64_t)(uintptr_t)A1);
    uint64_t b1 = chiplet_addr_transform((uint64_t)(uintptr_t)B1);
    uint64_t b2 = chiplet_addr_transform((uint64_t)(uintptr_t)B2);

    uint32_t n = 0;
    for (uint32_t c = 0; c < 2; c++) {
        bingo_task_t *load_a1 = gs_copy(c, a1, buf[c].a1, GS_A1_SIZE, sp[c][0]);
        bingo_task_t *load_b1 = gs_copy(c, b1, buf[c].b1, GS_B1_SIZE, sp[c][1]);
        bingo_task_t *load_b2 = gs_copy(c, b2, buf[c].b2, GS_B2_SIZE, sp[c][2]);
        bingo_task_t *gemm_d1 = gs_gemm(c, buf[c].a1, buf[c].b1, buf[c].d1, GS_M1, GS_K1, GS_N1, sp[c][3]);
        // D1 is the A input of the second GEMM (gemm_stacked_1cluster)
        bingo_task_t *gemm_d2 = gs_gemm(c, buf[c].d1, buf[c].b2, buf[c].d2, GS_M2, GS_K2, GS_N2, sp[c][4]);
        bingo_task_t *store_d2 = gs_copy(c, buf[c].d2, gs_d2_l3[c], GS_D2_SIZE, sp[c][5]);
        bingo_task_t *tasks[] = {load_a1, load_b1, load_b2, gemm_d1, gemm_d2, store_d2};
        for (uint32_t i = 0; i < GS_TASKS_PER_CLUSTER; i++) {
            if (!tasks[i]) {
                printf_safe("Chip(%x, %x): [Host] Error: the runtime could not create task %d of cluster %d\r\n",
                            get_current_chip_loc_x(), get_current_chip_loc_y(), i, c);
                return 0;
            }
            task_list[n++] = tasks[i];
        }
        bingo_task_add_depend(gemm_d1, load_a1);
        bingo_task_add_depend(gemm_d1, load_b1);
        bingo_task_add_depend(load_b2, gemm_d1);
        bingo_task_add_depend(gemm_d2, gemm_d1);
        bingo_task_add_depend(gemm_d2, load_b2);
        bingo_task_add_depend(store_d2, gemm_d2);
    }
    asm volatile("fence" ::: "memory");
    return n;
}

static uint32_t gs_check(void) {
    uint32_t total = 0;
    const volatile uint8_t *golden = (const volatile uint8_t *)(uintptr_t)
        chiplet_addr_transform((uint64_t)(uintptr_t)D2);
    asm volatile("fence" ::: "memory");
    for (uint32_t c = 0; c < 2; c++) {
        uint32_t errors = 0;
        const volatile uint8_t *output = (const volatile uint8_t *)(uintptr_t)gs_d2_l3[c];
        for (uint32_t i = 0; i < GS_D2_SIZE; i++) errors += output[i] != golden[i];
        printf_safe("[Host] Check [D2_cluster%d]: %s (%u / %u bytes)\r\n", c, errors ? "FAIL" : "PASS",
                    errors, GS_D2_SIZE);
        total += errors;
    }
    return total;
}

// Kernel Execution
int kernel_execution() {
    bingo_task_t *task_list[2 * GS_TASKS_PER_CLUSTER] = {0};
    uint32_t num_tasks = 0;
    if (get_current_chip_id() == 0) {
        num_tasks = __workload_gemm_stacked_2cluster(task_list);
        if (num_tasks == 0) return 1;
        bingo_runtime_schedule(task_list, num_tasks);
        printf("Chip(%x, %x): [Host] All tasks done.\n", get_current_chip_loc_x(), get_current_chip_loc_y());
        bingo_close_all_clusters(task_list, num_tasks);
        return gs_check() ? 1 : 0;
    }
    return 0;
}
