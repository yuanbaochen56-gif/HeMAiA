// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// B2: offload the same graph twice in one boot. Both offloads take the cold
// path of offload_bingo_hw.c (program the entry point, wake the clusters,
// wait for the kernel table, run the graph). Before the second one the host
// switches the test fault off, so the second offload is a healthy run, and
// clears the two soc_ctrl words the device writes once per run.
#include "offload_bingo_hw.h"
#if BINGO_TEST_CFG
#include "bingo_test_cfg.h"
#endif

// The second wake waits at most max(factor x first wake, min) host cycles
// for the kernel table (first wake = wake to kernel table ready).
#define B2_WAKE_BOUND_FACTOR 10
#define B2_WAKE_BOUND_MIN 1000000

#define B2_KERNEL_TAB_READY 0
#define B2_EXIT_CODE 2

static uintptr_t b2_soc_scratch(uint32_t index) {
    return (uintptr_t)chiplet_addr_transform((uint64_t)soc_ctrl_scratch_addr(index));
}

static uintptr_t b2_kernel_tab_ready() {
    return (uintptr_t)chiplet_addr_transform(
        (uint64_t)soc_ctrl_kernel_tab_scratch_addr(B2_KERNEL_TAB_READY));
}

static void b2_wake(uint8_t chip_id, comm_buffer_t *comm_buffer) {
    comm_buffer->lock = 0;
    comm_buffer->chip_id = chip_id;
    program_snitches(chip_id, comm_buffer);
    wakeup_snitches_cl(chip_id);
    asm volatile("fence" ::: "memory");
}

// 0 once the device has written the kernel table, -1 after bound cycles
// (bound 0: wait forever, as check_kernel_tab_ready does)
static int b2_wait_kernel_tab(uint64_t start, uint64_t bound) {
    while (readw(b2_kernel_tab_ready()) != 1) {
        if (bound != 0 && mcycle() - start > bound) return -1;
    }
    return 0;
}

int main() {
    uintptr_t current_chip_address_prefix =
        (uintptr_t)get_current_chip_baseaddress();
    uint8_t current_chip_id = get_current_chip_id();

    init_uart(current_chip_address_prefix, 32, 1);
    enable_vec();
    if (bingo_hemaia_system_mmap_init() < 0) {
        printf_safe("[B2] allocator init failed\r\n");
        return -1;
    }
    comm_buffer_t *comm_buffer =
        (comm_buffer_t *)bingo_get_l2_comm_buffer(current_chip_id);
    enable_sw_interrupts();

    // Offload 1
    uint64_t wake1 = mcycle();
    b2_wake(current_chip_id, comm_buffer);
    b2_wait_kernel_tab(wake1, 0);
    uint64_t ready1 = mcycle();
    int ret1 = kernel_execution();
    clear_host_sw_interrupt(current_chip_id);
    uint64_t end1 = mcycle();
    uint32_t exit1 = readw(b2_soc_scratch(B2_EXIT_CODE));
    printf_safe("[B2] offload 1 ret=%d wake=%lu ready=%lu end=%lu exit=0x%x\r\n",
                ret1, wake1, ready1, end1, exit1);

    // Boundary: the second offload runs without a fault
#if BINGO_TEST_CFG
    bingo_test_cfg.fault_gid = UINT32_MAX;
#endif
    writew(0, b2_kernel_tab_ready());
    writew(0, b2_soc_scratch(B2_EXIT_CODE));
    asm volatile("fence" ::: "memory");
    if (bingo_hemaia_system_mmap_init() < 0) {
        printf_safe("[B2] allocator init failed before offload 2\r\n");
        return -1;
    }

    // Offload 2
    uint64_t bound = B2_WAKE_BOUND_FACTOR * (ready1 - wake1);
    if (bound < B2_WAKE_BOUND_MIN) bound = B2_WAKE_BOUND_MIN;
    uint64_t wake2 = mcycle();
    b2_wake(current_chip_id, comm_buffer);
    if (b2_wait_kernel_tab(wake2, bound) != 0) {
        printf_safe("[B2] offload 2 not started: no kernel table %lu cycles after wake=%lu "
                    "exit=0x%x\r\n",
                    bound, wake2, readw(b2_soc_scratch(B2_EXIT_CODE)));
        return 3;
    }
    uint64_t ready2 = mcycle();
    int ret2 = kernel_execution();
    clear_host_sw_interrupt(current_chip_id);
    uint64_t end2 = mcycle();
    printf_safe("[B2] offload 2 ret=%d wake=%lu ready=%lu end=%lu exit=0x%x\r\n",
                ret2, wake2, ready2, end2, readw(b2_soc_scratch(B2_EXIT_CODE)));
    return ret1 | ret2;
}
