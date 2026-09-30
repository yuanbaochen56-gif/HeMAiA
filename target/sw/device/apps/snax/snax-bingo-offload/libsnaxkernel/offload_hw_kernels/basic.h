// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Fanchen Kong <fanchen.kong@kuleuven.be>
//
// Core-level bingo kernels that run on any core: dummy, entry_point, exit (the
// minimal control kernels the bingo-hw scheduler uses to mark task boundaries)
// and int32_add (a small compute kernel on the core itself).

#pragma once

#include "../macros.h"
#include "bingo_hw_heartbeat.h"

SNAX_LIB_DEFINE uint32_t __snax_bingo_kernel_dummy(void *arg){
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_START);
    uint32_t dummy_input = ((uint32_t *)arg)[0];
    bingo_kernel_scratchpad_t* sp = BINGO_GET_SP(arg, __snax_bingo_kernel_dummy_args_t);
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_END);
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_START);
    printf_safe("[Cluster %d Core %d]: Bingo Dummy Kernel: %d\r\n", snrt_cluster_idx(), snrt_cluster_core_idx(), dummy_input);
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_END);
    sp->return_value = 0;
    sp->num_return_values = 0;
    return BINGO_RET_SUCC;
}

SNAX_LIB_DEFINE uint32_t __snax_bingo_kernel_entry_point(void *arg){
    // This is a special kernel to indicate the bingo hw manager loop has started
    // In the future we can add some content here
    bingo_kernel_scratchpad_t* sp = BINGO_GET_SP(arg, __snax_bingo_kernel_entry_args_t);
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_START);
    printf_safe("[Cluster %d Core %d]: Start: \r\n", snrt_cluster_idx(), snrt_cluster_core_idx());
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_END);
    sp->return_value = 0;
    sp->num_return_values = 0;
    return BINGO_RET_SUCC;
}

SNAX_LIB_DEFINE uint32_t __snax_bingo_kernel_exit(void *arg){
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_START);
    const __snax_bingo_kernel_exit_args_t *a = (const __snax_bingo_kernel_exit_args_t *)arg;
    uint32_t exit_code = a->exit_code;
    bingo_kernel_scratchpad_t* sp = BINGO_GET_SP(arg, __snax_bingo_kernel_exit_args_t);
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_END);
    // After a core died, the HW manager may run its exit task on a substitute
    // core (replay / remap, or level 3 on another chiplet). Only the core the
    // task was compiled for leaves its loop; the substitute completes it and
    // keeps serving its own tasks.
    if ((a->assigned_chiplet_id != get_current_chip_id()) ||
        (a->assigned_cluster_id != snrt_cluster_idx()) ||
        (a->assigned_core_id != snrt_cluster_core_idx())) {
        printf_safe("[Cluster %d Core %d]: Exit task of cluster %d core %d taken over, not exiting (chip %d)\r\n",
                    snrt_cluster_idx(), snrt_cluster_core_idx(),
                    a->assigned_cluster_id, a->assigned_core_id, a->assigned_chiplet_id);
        sp->return_value = BINGO_RET_SUCC;
        sp->num_return_values = 0;
        return BINGO_RET_SUCC;
    }
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_START);
    printf_safe("[Cluster %d Core %d]: Exiting with code %d\r\n", snrt_cluster_idx(), snrt_cluster_core_idx(), exit_code);
    BINGO_TRACE_MARKER(BINGO_TRACE_DUMMY_KERNEL_END);
    sp->return_value = BINGO_RET_EXIT;
    sp->num_return_values = 0;
    return BINGO_RET_EXIT;
}

// C[i] = A[i] + B[i] for num_elements int32 values, computed by the core itself,
// so any core can run it. The buffers must be addressable by the core (L1).
SNAX_LIB_DEFINE uint32_t __snax_bingo_kernel_int32_add(void *arg){
    BINGO_SW_GUARD_CHECK(arg, __snax_bingo_kernel_int32_add_args_t);
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_START);
    const __snax_bingo_kernel_int32_add_args_t *a = (const __snax_bingo_kernel_int32_add_args_t *)arg;
    const int32_t *in_a = (const int32_t *)(uintptr_t)a->a_addr;
    const int32_t *in_b = (const int32_t *)(uintptr_t)a->b_addr;
    int32_t *out_c = (int32_t *)(uintptr_t)a->c_addr;
    bingo_kernel_scratchpad_t* sp = BINGO_GET_SP(arg, __snax_bingo_kernel_int32_add_args_t);
    BINGO_TRACE_MARKER(BINGO_TRACE_KERNEL_ARG_PARSE_END);
    for (uint32_t i = 0; i < a->num_elements; i++) {
        out_c[i] = in_a[i] + in_b[i];
        // Keep the watchdog quiet on long vectors
        if ((i & 0xff) == 0xff) bingo_hw_manager_heartbeat(1);
    }
    sp->return_value = a->c_addr;
    sp->num_return_values = a->num_elements;
    return BINGO_RET_SUCC;
}
