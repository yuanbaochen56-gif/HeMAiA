// Copyright 2025 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// B2: offload the same graph twice in one boot. Both offloads take the cold
// path of offload_bingo_hw.c (program the entry point, wake the clusters,
// wait for the kernel table, run the graph). Before the second one the host
// switches the test fault off (unless T1 user word 0 bit 0 keeps it), so the
// second offload is a healthy run, and clears the two soc_ctrl words the
// device writes once per run. T1 user word 1 delays the boundary.
//
// With the accelerator reset in the platform (OCCAMY_ACC_RESET_CHANNEL) the
// host also resets the quadrant and all clusters before the second offload and
// restores the device image, whose .data holds the device's global barrier
// state: every core then starts the second offload from the boot ROM.
#include "offload_bingo_hw.h"
#if BINGO_TEST_CFG
#include "bingo_test_cfg.h"
#endif
#ifdef OCCAMY_ACC_RESET_CHANNEL
#include "hemaia_clk_rst_controller.h"
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

#ifdef OCCAMY_ACC_RESET_CHANNEL
// The device image as loaded: [snitch_main, __l3_heap_start)
static uint64_t b2_image_start() {
    return chiplet_addr_transform((uint64_t)(uintptr_t)snitch_main);
}

static uint64_t b2_image_size() {
    return chiplet_addr_transform((uint64_t)(uintptr_t)&__l3_heap_start) - b2_image_start();
}

// Copy of the image in the first L3 heap block, which the allocator hands out
// again after every heap init
static uint64_t b2_image_copy(uint8_t chip_id) {
    return bingo_l3_alloc(chip_id, b2_image_size());
}

// Reset the quadrant and all clusters. A host-init flag of the quadrant reads
// 0 again once the quadrant is out of reset; until then the read waits at the
// isolation of the quadrant ports.
static void b2_acc_reset() {
    uintptr_t flag = (uintptr_t)chiplet_addr_transform((uint64_t)quad_ctrl_host_init_done_addr());
    writew(1, flag);
    asm volatile("fence" ::: "memory");
    reset_clk_domain(OCCAMY_ACC_RESET_CHANNEL);
    asm volatile("fence" ::: "memory");
    while (readw(flag) != 0) {
    }
}
#endif

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
#ifdef OCCAMY_ACC_RESET_CHANNEL
    sys_dma_blk_memcpy(current_chip_id, b2_image_copy(current_chip_id), b2_image_start(),
                       b2_image_size());
#endif

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
    if (!(bingo_test_cfg.user[0] & 1)) bingo_test_cfg.fault_gid = UINT32_MAX;
    // T1 user word 1: host cycles to wait first, so that a stalled core can
    // come back before the boundary
    uint64_t wait_start = mcycle();
    while (mcycle() - wait_start < bingo_test_cfg.user[1]) {
    }
#endif
#ifdef OCCAMY_ACC_RESET_CHANNEL
    // Reset first: a core still running could write the two words again
    uint64_t reset_start = mcycle();
    b2_acc_reset();
    uint64_t reset_end = mcycle();
#endif
    writew(0, b2_kernel_tab_ready());
    writew(0, b2_soc_scratch(B2_EXIT_CODE));
    asm volatile("fence" ::: "memory");
    if (bingo_hemaia_system_mmap_init() < 0) {
        printf_safe("[B2] allocator init failed before offload 2\r\n");
        return -1;
    }
#ifdef OCCAMY_ACC_RESET_CHANNEL
    sys_dma_blk_memcpy(current_chip_id, b2_image_start(), b2_image_copy(current_chip_id),
                       b2_image_size());
    asm volatile("fence" ::: "memory");
    uint64_t restored = mcycle();
#endif

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
#ifdef OCCAMY_ACC_RESET_CHANNEL
    printf_safe("[B2] accelerator reset start=%lu end=%lu restored=%lu image=%lu\r\n",
                reset_start, reset_end, restored, b2_image_size());
#endif
    return ret1 | ret2;
}
