// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Scheduling micro-benchmark on the software runtime. Same graph as the
// hardware-manager workload offload_bingo_hw/.../sched_chain_2cluster:
// N iDMA copies of BINGO_SCHED_CHUNK bytes from an L3 vector into cluster L1,
//   chain    (BINGO_SCHED_PAR 0): copy i moves chunk i into the cluster-0 buffer
//            after copy i - 1; cluster 1 copies chunk 0 into its own buffer at the
//            same time;
//   parallel (BINGO_SCHED_PAR 1): independent copies, copy i on cluster i % 2,
//            chunk i to slot i / 2 of that cluster's buffer.
// The host fills the source with the pattern of the hardware workload before it
// creates the tasks and checks every cluster buffer after the schedule.

#pragma once
#include "libbingo/bingo_api.h"
#include "host.h"

#ifndef BINGO_SCHED_N
#define BINGO_SCHED_N 4
#endif
#ifndef BINGO_SCHED_PAR
#define BINGO_SCHED_PAR 0
#endif
#define BINGO_SCHED_CHUNK 64
#if BINGO_SCHED_N < 1 || BINGO_SCHED_N > 32
#error "BINGO_SCHED_N must be 1..32"
#endif

static uint64_t sched_src;
static uint64_t sched_dst[2];

static inline uint8_t sched_pattern(uint32_t index) {
    return (uint8_t)((7u * index + index / 256u) & 0xFFu);
}

static inline uint32_t sched_cluster(uint32_t i) { return BINGO_SCHED_PAR ? i % 2 : 0; }
static inline uint32_t sched_slot(uint32_t i) { return BINGO_SCHED_PAR ? i / 2 : i; }

static bingo_task_t *sched_copy(uint32_t fn, uint32_t cluster, uint32_t src_chunk, uint32_t dst_slot) {
    __snax_kernel_idma_1d_copy_args_t *args = (__snax_kernel_idma_1d_copy_args_t *)(uintptr_t)
        bingo_l3_alloc(0, sizeof(__snax_kernel_idma_1d_copy_args_t));
    uint64_t src = sched_src + (uint64_t)src_chunk * BINGO_SCHED_CHUNK;
    uint64_t dst = sched_dst[cluster] + (uint64_t)dst_slot * BINGO_SCHED_CHUNK;
    args->src_addr_hi = HIGH32(src);
    args->src_addr_lo = LOW32(src);
    args->dst_addr_hi = HIGH32(dst);
    args->dst_addr_lo = LOW32(dst);
    args->size = BINGO_SCHED_CHUNK;
    return bingo_task_create(fn, (uint32_t)(uintptr_t)args, 0, cluster);
}

uint32_t __workload_sched_chain(bingo_task_t **task_list) {
    check_kernel_tab_ready();
    printf_safe("Chip(%x, %x): [Host] Preparing scheduling benchmark: %d copies, %s\r\n",
                get_current_chip_loc_x(), get_current_chip_loc_y(), BINGO_SCHED_N,
                BINGO_SCHED_PAR ? "parallel" : "chain");
    uint32_t fn = get_device_function("__snax_kernel_idma_1d_copy");
    if (fn == SNAX_SYMTAB_END_FN_ADDR) {
        printf_safe("Error: Kernel symbol lookup failed!\r\n");
        return 0;
    }
    // Source vector in L3, one buffer per cluster in L1
    uint8_t *src = (uint8_t *)(uintptr_t)bingo_l3_alloc(0, BINGO_SCHED_N * BINGO_SCHED_CHUNK);
    for (uint32_t i = 0; i < BINGO_SCHED_N * BINGO_SCHED_CHUNK; i++) src[i] = sched_pattern(i);
    sched_src = chiplet_addr_transform((uint64_t)(uintptr_t)src);
    uint32_t used[2] = {0, 0};
    for (uint32_t i = 0; i < BINGO_SCHED_N; i++) used[sched_cluster(i)]++;
    if (!BINGO_SCHED_PAR) used[1] = 1;
    for (uint32_t c = 0; c < 2; c++) sched_dst[c] = bingo_l1_alloc(0, c, used[c] * BINGO_SCHED_CHUNK);

    uint32_t n = 0;
    bingo_task_t *prev = NULL;
    for (uint32_t i = 0; i < BINGO_SCHED_N; i++) {
        bingo_task_t *t = sched_copy(fn, sched_cluster(i), i, sched_slot(i));
        if (!BINGO_SCHED_PAR && prev) bingo_task_add_depend(t, prev);
        prev = t;
        task_list[n++] = t;
    }
    if (!BINGO_SCHED_PAR) task_list[n++] = sched_copy(fn, 1, 0, 0);
    asm volatile("fence" ::: "memory");
    return n;
}

static uint32_t sched_check(void) {
    uint32_t errors = 0;
    asm volatile("fence" ::: "memory");
    for (uint32_t i = 0; i < BINGO_SCHED_N; i++) {
        volatile uint8_t *dst = (volatile uint8_t *)(uintptr_t)
            (sched_dst[sched_cluster(i)] + (uint64_t)sched_slot(i) * BINGO_SCHED_CHUNK);
        for (uint32_t b = 0; b < BINGO_SCHED_CHUNK; b++)
            if (dst[b] != sched_pattern(i * BINGO_SCHED_CHUNK + b)) errors++;
    }
    if (!BINGO_SCHED_PAR) {
        volatile uint8_t *dst = (volatile uint8_t *)(uintptr_t)sched_dst[1];
        for (uint32_t b = 0; b < BINGO_SCHED_CHUNK; b++)
            if (dst[b] != sched_pattern(b)) errors++;
    }
    printf_safe("Chip(%x, %x): [Host] Scheduling benchmark check: %s (%d errors)\r\n",
                get_current_chip_loc_x(), get_current_chip_loc_y(), errors ? "FAIL" : "PASS", errors);
    return errors;
}

// Kernel Execution
int kernel_execution(){
    bingo_task_t *task_list[64] = {0};
    uint32_t num_tasks = 0;
    if (get_current_chip_id() == 0) {
        num_tasks = __workload_sched_chain(task_list);
        if (num_tasks == 0) return 1;
        bingo_runtime_schedule(task_list, num_tasks);
        printf("Chip(%x, %x): [Host] All tasks done.\n", get_current_chip_loc_x(), get_current_chip_loc_y());
        bingo_close_all_clusters(task_list, num_tasks);
        return sched_check() ? 1 : 0;
    }
    return 0;
}
