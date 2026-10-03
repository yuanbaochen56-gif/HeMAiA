#include "libbingo/bingo_api.h"
#include "requant.h"

uint64_t __host_bingo_kernel_early_exit_requant(void *arg) {
    __host_bingo_kernel_early_exit_requant_args_t *args = arg;
    const int32_t *src = (const int32_t *)(uintptr_t)args->src_addr;
    int8_t *dst = (int8_t *)(uintptr_t)args->dst_addr;
    uint64_t result = BINGO_RET_FAIL;
    if (args->shift < 31 && args->count <= args->dst_size) {
        for (uint32_t i = 0; i < args->count; i++)
            dst[i] = early_exit_requant(src[i], args->shift);
        // The GEMM A reader fetches a full bank beat past the logical row.
        for (uint32_t i = args->count; i < args->dst_size; i++)
            dst[i] = 0;
        result = BINGO_RET_SUCC;
    }
    bingo_kernel_scratchpad_t *sp =
        (bingo_kernel_scratchpad_t *)(uintptr_t)args->scratchpad_ptr;
    sp->return_value = result;
    sp->num_return_values = 0;
    return result;
}
