#pragma once

#ifdef BINGO_SDC_RECOVER
#include <stdint.h>
#include "libbingo/host_kernel_args.h"

/* Only the new copy workload uses this reliable-host resolver. No golden data
 * or fault diagnosis enters the vote. Numeric values follow the sealed ABI. */
static inline void __sdc_recover_fence(void) {
#ifdef __riscv
    asm volatile("fence rw, rw" ::: "memory");
#else
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
#endif
}

static inline uint64_t __sdc_recover_cycle(void) {
#ifdef __riscv
    uint64_t cycle;
    asm volatile("csrr %0, mcycle" : "=r"(cycle) :: "memory");
    return cycle;
#else
    return 0; /* Native semantic tests do not measure hardware time. */
#endif
}

static inline int __sdc_recover_range(uint64_t addr, uint64_t size) {
    return addr && addr <= UINT64_MAX - size;
}

static inline int __sdc_recover_overlap(uint64_t a, uint64_t n,
                                      uint64_t b, uint64_t m) {
    return a < b + m && b < a + n;
}

static inline void __sdc_recover_report(volatile bingo_sdc_recovery_state_t *s,
                                      uint64_t status_addr) {
    if (s->report_count) return; /* Re-entry cannot emit a second report. */
    s->report_count = 1;
    __sdc_recover_fence();
    printf_safe("[Host] SDC_RECOVER tx=%u enabled=%u code=%u detect_valid=%u "
                "third=%u selected=%u commit=%u reports=%u outcome=%u "
                "failure=%u phase=%u status_addr=0x%lx rstart=%lu status_cc=%lu "
                "third_start=%lu third_end=%lu vote_end=%lu publish_end=%lu\r\n",
                s->transaction, s->enabled, s->detect_code, s->detect_valid,
                s->third_count, s->selected, s->commit_count, s->report_count,
                s->outcome, s->failure, s->phase, status_addr,
                s->resolver_start_cc, s->status_read_cc, s->third_start_cc,
                s->third_end_cc, s->vote_end_cc, s->publish_end_cc);
    __sdc_recover_fence();
}

static inline uint64_t __sdc_recover_fail(volatile bingo_sdc_recovery_state_t *s,
                                       uint32_t failure, uint64_t status_addr) {
    s->failure = failure;
    s->outcome = 4;
    s->phase = 3;
    __sdc_recover_report(s, status_addr);
    return BINGO_RET_FAIL;
}

static inline uint64_t __host_bingo_kernel_sdc_recover_init(void *arg) {
    const __host_bingo_kernel_sdc_recover_init_args_t *a = arg;
    if (!a || a->transactions != 2 || a->enabled > 1 ||
        (a->state_addr & 7) || (a->status0_addr & 3) || (a->status1_addr & 3) ||
        !__sdc_recover_range(a->state_addr, 192) ||
        !__sdc_recover_range(a->status0_addr, 4) ||
        !__sdc_recover_range(a->status1_addr, 4) ||
        a->status1_addr != a->status0_addr + 4 ||
        __sdc_recover_overlap(a->state_addr, 192, a->status0_addr, 8))
        return BINGO_RET_FAIL;
    volatile bingo_sdc_recovery_state_t *states =
        (volatile bingo_sdc_recovery_state_t *)(uintptr_t)a->state_addr;
    for (uint32_t tx = 0; tx < 2; ++tx) {
        volatile uint8_t *bytes = (volatile uint8_t *)&states[tx];
        for (uint32_t i = 0; i < sizeof(states[tx]); ++i) bytes[i] = 0;
        states[tx].abi_version = 1;
        states[tx].transaction = tx;
        states[tx].enabled = (uint32_t)a->enabled;
        states[tx].detect_code = UINT32_MAX;
    }
    *(volatile uint32_t *)(uintptr_t)a->status0_addr = a->enabled ? UINT32_MAX : 0;
    *(volatile uint32_t *)(uintptr_t)a->status1_addr = a->enabled ? UINT32_MAX : 0;
    __sdc_recover_fence();
    return BINGO_RET_SUCC;
}

static inline uint64_t __host_bingo_kernel_sdc_resolve_copy(void *arg) {
    const __host_bingo_kernel_sdc_resolve_copy_args_t *a = arg;
    if (!a || (a->state_addr & 7) ||
        !__sdc_recover_range(a->state_addr, 96)) return BINGO_RET_FAIL;
    volatile bingo_sdc_recovery_state_t *s =
        (volatile bingo_sdc_recovery_state_t *)(uintptr_t)a->state_addr;
    __sdc_recover_fence(); /* Acquire device DMA/status after compare DONE. */
    if (s->phase != 0) return __sdc_recover_fail(s, 4, a->status_addr);
    if (s->commit_count) return __sdc_recover_fail(s, 5, a->status_addr);
    if (s->report_count) return __sdc_recover_fail(s, 6, a->status_addr);
    if (s->abi_version != 1 || s->transaction != a->transaction ||
        s->enabled > 1 || s->third_count || s->detect_valid ||
        s->detect_code != UINT32_MAX || s->selected || s->outcome || s->failure)
        return __sdc_recover_fail(s, 7, a->status_addr);
    const uint64_t addresses[7] = {a->src_addr, a->primary_addr, a->shadow_addr,
        a->third_addr, a->canonical_addr, a->status_addr, a->state_addr};
    const uint64_t sizes[7] = {64, 64, 64, 64, 64, 4, 96};
    if (a->size != 64 || a->transaction > 1 || (a->status_addr & 3))
        return __sdc_recover_fail(s, 1, a->status_addr);
    for (uint32_t i = 0; i < 7; ++i) {
        if (!__sdc_recover_range(addresses[i], sizes[i]))
            return __sdc_recover_fail(s, 1, a->status_addr);
        for (uint32_t j = 0; j < i; ++j)
            if (__sdc_recover_overlap(addresses[i], sizes[i], addresses[j], sizes[j]))
                return __sdc_recover_fail(s, 1, a->status_addr);
    }
    s->phase = 1;
    s->resolver_start_cc = __sdc_recover_cycle();
    const volatile uint8_t *src = (const volatile uint8_t *)(uintptr_t)a->src_addr;
    const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t)a->primary_addr;
    const volatile uint8_t *shadow = (const volatile uint8_t *)(uintptr_t)a->shadow_addr;
    volatile uint8_t *third = (volatile uint8_t *)(uintptr_t)a->third_addr;
    const volatile uint8_t *chosen = p;
    s->status_read_cc = __sdc_recover_cycle();
    if (s->enabled) {
        const uint32_t code = *(const volatile uint32_t *)(uintptr_t)a->status_addr;
        s->detect_code = code; /* The original detector word is never erased. */
        s->detect_valid = 1;
        uint32_t first_difference = 0;
        for (uint32_t i = 0; i < 64; ++i)
            if (p[i] != shadow[i] && !first_difference) first_difference = i + 1;
        if (code > 64 || code != first_difference)
            return __sdc_recover_fail(s, 2, a->status_addr);
        if (code) {
            s->third_count = 1;
            s->third_start_cc = __sdc_recover_cycle();
            for (uint32_t i = 0; i < 64; ++i) third[i] = src[i];
            __sdc_recover_fence();
            s->third_end_cc = __sdc_recover_cycle();
            uint32_t p_equal = 1, s_equal = 1;
            for (uint32_t i = 0; i < 64; ++i) {
                p_equal &= p[i] == third[i];
                s_equal &= shadow[i] == third[i];
            }
            if (p_equal == s_equal) return __sdc_recover_fail(s, 3, a->status_addr);
            chosen = p_equal ? p : shadow;
            s->selected = p_equal ? 1 : 2;
            s->outcome = 3;
        } else {
            s->selected = 1;
            s->outcome = 2;
        }
    } else {
        s->selected = 1;
        s->outcome = 1;
    }
    s->vote_end_cc = __sdc_recover_cycle();
    volatile uint8_t *canonical = (volatile uint8_t *)(uintptr_t)a->canonical_addr;
    for (uint32_t i = 0; i < 64; ++i) canonical[i] = chosen[i];
    __sdc_recover_fence(); /* Entire buffer visible before publication metadata. */
    s->publish_end_cc = __sdc_recover_cycle();
    s->commit_count = 1;
    s->phase = 2;
    __sdc_recover_report(s, a->status_addr);
    return BINGO_RET_SUCC; /* Only this existing return permits host DONE. */
}
#endif
