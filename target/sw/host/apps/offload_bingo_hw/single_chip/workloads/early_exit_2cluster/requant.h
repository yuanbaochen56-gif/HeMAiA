// Workload-local int32 -> int8 bridge. The supported compilers use arithmetic
// signed right shift; the native fixture verifies negative values and clipping.
#pragma once
#include <stdint.h>

static inline int8_t early_exit_requant(int32_t value, uint32_t shift) {
    int32_t scaled = value >> shift;
    if (scaled < -128) scaled = -128;
    if (scaled > 127) scaled = 127;
    return (int8_t)scaled;
}

typedef struct {
    uint64_t src_addr;
    uint64_t dst_addr;
    uint32_t count;
    uint32_t shift;
    uint32_t dst_size;
    uint64_t scratchpad_ptr;
} __host_bingo_kernel_early_exit_requant_args_t;

uint64_t __host_bingo_kernel_early_exit_requant(void *arg);
