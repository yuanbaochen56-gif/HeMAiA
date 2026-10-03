#pragma once
#include <limits.h>
#include <stdint.h>

#ifndef BINGO_EE_SAMPLE
#define BINGO_EE_SAMPLE 0
#endif

static inline uint64_t early_exit_conf_margin(const int32_t *values, uint32_t count) {
    int32_t first = values[0], second = INT32_MIN;
    for (uint32_t i = 1; i < count; i++) {
        if (values[i] >= first) {
            second = first;
            first = values[i];
        } else if (values[i] > second) {
            second = values[i];
        }
    }
    return (uint64_t)((int64_t)first - (int64_t)second);
}

static inline void early_exit_conf_select(const int8_t *samples, int8_t *dst,
                                         uint32_t size, uint32_t sample) {
    for (uint32_t i = 0; i < size; i++)
        dst[i] = samples[sample * size + i];
}

typedef struct {
    uint64_t samples_addr;
    uint64_t dst_addr;
    uint32_t sample_size;
    uint32_t sample_count;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ee_select_args_t;

typedef struct {
    uint64_t src_addr;
    uint64_t decision_addr;
    uint32_t count;
    uint32_t threshold;
    uint32_t cerf_controlled_mask;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_ee_conf_gate_args_t;

uint32_t early_exit_conf_sample(void);
uint64_t __host_bingo_kernel_ee_select(void *arg);
uint64_t __host_bingo_kernel_ee_conf_gate(void *arg);
